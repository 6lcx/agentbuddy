import time
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from agentbuddy.persistence.schema import credentials, models, providers, settings_table


class DomainError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def public_provider(row):
    return {k: v for k, v in dict(row).items() if k != "credential_id"} | {
        "credential_configured": True
    }


class Catalog:
    def __init__(self, db):
        self.db = db

    def provider_list(self):
        with self.db.engine.connect() as conn:
            return [public_provider(r) for r in conn.execute(select(providers)).mappings()]

    def save_provider(self, data, provider_id=None):
        data = dict(data)
        secret = data.pop("api_key", None)
        with self.db.write() as conn:
            if provider_id:
                old = (
                    conn.execute(select(providers).where(providers.c.id == provider_id))
                    .mappings()
                    .first()
                )
                if not old:
                    raise DomainError("NOT_FOUND", "提供方不存在", 404)
                if data.pop("revision") != old["revision"]:
                    raise DomainError("REVISION_CONFLICT", "配置已被修改，请刷新")
                credential_id = old["credential_id"]
                revision = old["revision"] + 1
            else:
                provider_id, credential_id, revision = str(uuid4()), None, 1
            if secret:
                credential_id = str(uuid4())
                conn.execute(insert(credentials).values(id=credential_id, secret=secret))
            if not credential_id:
                raise DomainError("MISSING_CREDENTIAL", "请填写密钥", 422)
            row = dict(id=provider_id, credential_id=credential_id, revision=revision, **data)
            if revision == 1:
                conn.execute(insert(providers).values(**row))
            else:
                conn.execute(update(providers).where(providers.c.id == provider_id).values(**row))
            return public_provider(row)

    def model_list(self):
        with self.db.engine.connect() as conn:
            rows = conn.execute(
                select(
                    models,
                    providers.c.revision.label("provider_revision"),
                    providers.c.enabled.label("provider_enabled"),
                    providers.c.name.label("provider_name"),
                    providers.c.provider_type,
                ).join(providers)
            ).mappings()
            result = []
            for row in rows:
                value = dict(row)
                value["available"] = bool(
                    row["enabled"]
                    and row["provider_enabled"]
                    and row["test_status"] == "passed"
                    and row["tested_model_revision"] == row["revision"]
                    and row["tested_provider_revision"] == row["provider_revision"]
                )
                if (
                    row["tested_provider_revision"] != row["provider_revision"]
                    and row["test_status"] == "passed"
                ):
                    value["test_status"] = "stale"
                result.append(value)
            return result

    def save_model(self, data, model_key=None):
        data = dict(data)
        try:
            with self.db.write() as conn:
                if not conn.scalar(
                    select(providers.c.id).where(providers.c.id == data["provider_id"])
                ):
                    raise DomainError("NOT_FOUND", "提供方不存在", 404)
                if model_key:
                    old = (
                        conn.execute(select(models).where(models.c.id == model_key))
                        .mappings()
                        .first()
                    )
                    if not old:
                        raise DomainError("NOT_FOUND", "模型不存在", 404)
                    if data.pop("revision") != old["revision"]:
                        raise DomainError("REVISION_CONFLICT", "模型配置已被修改，请刷新")
                    revision = old["revision"] + 1
                else:
                    model_key, revision = str(uuid4()), 1
                row = dict(
                    id=model_key,
                    revision=revision,
                    test_status="untested",
                    capabilities_json={},
                    tested_provider_revision=None,
                    tested_model_revision=None,
                    tested_at=None,
                    **data,
                )
                if revision == 1:
                    conn.execute(insert(models).values(**row))
                else:
                    conn.execute(update(models).where(models.c.id == model_key).values(**row))
                return row
        except IntegrityError as exc:
            raise DomainError("DUPLICATE_MODEL", "该提供方已有相同模型名") from exc

    def snapshot(self, conn, model_key, require_test=True):
        model = conn.execute(select(models).where(models.c.id == model_key)).mappings().first()
        if not model:
            raise DomainError("MODEL_REQUIRED", "请选择已配置的模型", 422)
        provider = (
            conn.execute(select(providers).where(providers.c.id == model["provider_id"]))
            .mappings()
            .one()
        )
        if not model["enabled"] or not provider["enabled"]:
            raise DomainError("MODEL_DISABLED", "所选模型或提供方已停用")
        if require_test and not (
            model["test_status"] == "passed"
            and model["tested_model_revision"] == model["revision"]
            and model["tested_provider_revision"] == provider["revision"]
        ):
            raise DomainError("MODEL_UNTESTED", "请先在设置页完成模型文本与工具探测")
        return {"model": dict(model), "provider": dict(provider)}

    def test_payload(self, model_key):
        with self.db.engine.connect() as conn:
            return self.snapshot(conn, model_key, require_test=False)

    def record_test(self, snapshot, passed):
        model, provider = snapshot["model"], snapshot["provider"]
        with self.db.write() as conn:
            # Results belong to the tested revisions, never a subsequently edited configuration.
            conn.execute(
                update(models)
                .where(models.c.id == model["id"], models.c.revision == model["revision"])
                .values(
                    tested_at=time.time(),
                    test_status="passed" if passed else "failed",
                    tested_model_revision=model["revision"],
                    tested_provider_revision=provider["revision"],
                    capabilities_json={
                        "text": passed,
                        "tools": passed,
                        "streaming": passed,
                        "vision": False,
                    },
                )
            )

    def secret(self, credential_id):
        with self.db.engine.connect() as conn:
            return conn.scalar(
                select(credentials.c.secret).where(credentials.c.id == credential_id)
            )

    def settings(self, conn=None):
        if conn is None:
            with self.db.engine.connect() as connection:
                return self.settings(connection)
        row = (
            conn.execute(select(settings_table).where(settings_table.c.key == "default_model"))
            .mappings()
            .first()
        )
        return {
            "revision": row["revision"] if row else 0,
            "default_model_id": row["value_json"] if row else None,
        }

    def set_default(self, model_key, revision):
        with self.db.write() as conn:
            current = self.settings(conn)
            if current["revision"] != revision:
                raise DomainError("REVISION_CONFLICT", "设置已被修改，请刷新")
            self.snapshot(conn, model_key)
            row = dict(key="default_model", value_json=model_key, revision=revision + 1)
            if revision:
                conn.execute(
                    update(settings_table)
                    .where(settings_table.c.key == "default_model")
                    .values(**row)
                )
            else:
                conn.execute(insert(settings_table).values(**row))
            return {"revision": revision + 1, "default_model_id": model_key}

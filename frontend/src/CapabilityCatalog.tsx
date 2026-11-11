import { useState } from 'react';
import { Button, Empty, Input, Modal, Spin, Tabs, Tag } from 'antd';
import type { Capability } from './Capabilities';
import { CapabilityVisual, capabilityInfo } from './CapabilityVisual';

export function CapabilityCatalog({ kind, onKind, rows, loading, onManage, onUse }: { kind: string; onKind: (kind: string) => void; rows: Capability[]; loading: boolean; onManage: () => void; onUse: (row: Capability, prompt?: string) => void }) {
  const [search, setSearch] = useState('');
  const [onlyEnabled, setOnlyEnabled] = useState(false);
  const [detail, setDetail] = useState<string>();
  const selected = rows.find(row => row.id === detail);
  const info = selected && capabilityInfo(selected);
  const labels: Record<string, string> = { expert: '专家', skill: '技能', connector: '连接器' };
  const filtered = rows.filter(row => (!onlyEnabled || row.config_json.enabled) && `${row.config_json.name} ${capabilityInfo(row).summary}`.toLowerCase().includes(search.toLowerCase()));
  return <div className="capability-catalog">
    <div className="catalog-toolbar"><Tabs activeKey={kind} onChange={value => { onKind(value); setDetail(undefined); setSearch(''); }} items={[{ key: 'expert', label: '◉ 专家' }, { key: 'skill', label: '✧ 技能' }, { key: 'connector', label: '↗ 连接器' }]} /><div className="catalog-tools"><Input.Search aria-label="搜索目录" placeholder={`搜索${labels[kind]}`} value={search} onChange={e => setSearch(e.target.value)} /><Button onClick={onManage}>管理{labels[kind]}</Button></div></div>
    <div className="catalog-intro"><div><h1>{kind === 'expert' ? '找到擅长这件事的伙伴' : kind === 'skill' ? '给你的想法，多一种实现方式' : '连接工具，让信息流动起来'}</h1><p>{kind === 'expert' ? '选择一个专家，从一句话开始。' : kind === 'skill' ? '文档、表格、演示与代码，按需加入对话。' : '使用已配置的服务查询资料、调用工具，再把结果带回项目。'}</p></div></div>
    <div className="catalog-filter"><div><button className={!onlyEnabled ? 'selected' : ''} onClick={() => setOnlyEnabled(false)}>全部</button><button className={onlyEnabled ? 'selected' : ''} onClick={() => setOnlyEnabled(true)}>已启用</button></div><span>{filtered.length} 项{labels[kind]}</span></div>
    {loading ? <Spin /> : !filtered.length ? <Empty description={search ? '没有找到匹配的能力' : '这里还没有能力，点击管理添加'} /> : <div className="catalog-grid">{filtered.map(row => { const meta = capabilityInfo(row); return <button className="catalog-card" key={row.id} onClick={() => setDetail(row.id)}><div className="catalog-card-top"><CapabilityVisual kind={row.kind} name={row.config_json.name} /><div><h2>{row.config_json.name}</h2><p>{meta.summary}</p></div></div><div className="catalog-tags">{meta.tags.map(tag => <span key={tag}>{tag}</span>)}</div><div className="catalog-card-bottom"><span className={row.config_json.enabled ? 'is-enabled' : ''}>{row.config_json.enabled ? '● 已启用' : '○ 未启用'}</span><span>查看用途与示例</span></div></button>; })}</div>}
    <Modal open={!!selected} onCancel={() => setDetail(undefined)} footer={null} width={710} className="capability-detail" title={null}>
      {selected && info && <><div className="detail-heading"><CapabilityVisual kind={selected.kind} name={selected.config_json.name} /><div><h2>{selected.config_json.name}</h2><Button type="primary" disabled={!selected.config_json.enabled} onClick={() => onUse(selected)}>{kind === 'expert' ? '召唤专家' : '加入对话'}</Button>{!selected.config_json.enabled && <Button type="link" onClick={() => { setDetail(undefined); onManage(); }}>前往启用</Button>}</div></div><p className="detail-summary">{info.summary}</p><div className="catalog-tags">{info.tags.map(tag => <Tag key={tag}>{tag}</Tag>)}</div><div className="detail-divider"/><h3>{kind === 'expert' ? '这位专家可以帮你' : '试试这样说'}</h3><p className="detail-hint">点击示例填入对话，补充你的要求后再发送。</p><div className="detail-examples">{info.examples.map(example => <button key={example} disabled={!selected.config_json.enabled} onClick={() => onUse(selected, example)}><span>“{example}”</span></button>)}</div>{kind === 'connector' && <p className="detail-hint">实际可用操作取决于连接配置及服务提供的工具。</p>}<details className="detail-more"><summary>更多信息</summary><p>修订 {selected.revision} · {selected.config_json.enabled ? '已启用' : '未启用'}</p><code>{selected.id}</code></details></>}
    </Modal>
  </div>;
}

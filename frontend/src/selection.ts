import type { Choices } from './RunPanel';

export function toggleCapability(selection: Choices['selection'], kind: 'skills' | 'connectors', id: string, enabled: boolean): Choices['selection'] {
  const add = kind === 'skills' ? 'add_skills' : 'add_connectors';
  const remove = kind === 'skills' ? 'remove_skills' : 'remove_connectors';
  return { ...selection, [add]: [...new Set([...(selection[add] ?? []).filter(key => key !== id), ...(enabled ? [id] : [])])], [remove]: [...new Set([...(selection[remove] ?? []).filter(key => key !== id), ...(!enabled ? [id] : [])])] };
}

export function effectiveIds(defaults: string[], added: string[] = [], removed: string[] = []) {
  return new Set([...defaults, ...added].filter(id => !removed.includes(id)));
}

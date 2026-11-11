import { describe, expect, it } from 'vitest';
import { effectiveIds, toggleCapability } from './selection';

describe('session capability selection', () => {
  it('resolves legacy conflicts with removal taking precedence', () => {
    expect([...effectiveIds(['excel', 'word'], ['excel', 'code'], ['excel'])]).toEqual(['word', 'code']);
  });
  it('enabling or disabling clears the opposing override', () => {
    const enabled = toggleCapability({ add_skills: ['excel'], remove_skills: ['excel'] }, 'skills', 'excel', true);
    expect(enabled).toEqual({ add_skills: ['excel'], remove_skills: [] });
    expect(toggleCapability(enabled, 'skills', 'excel', false)).toEqual({ add_skills: [], remove_skills: ['excel'] });
  });
  it('manual choices survive changing expert defaults', () => {
    const manual = { add_skills: ['excel'], remove_skills: ['word'] };
    expect([...effectiveIds(['code', 'word'], manual.add_skills, manual.remove_skills)]).toEqual(['code', 'excel']);
    expect([...effectiveIds(['slides', 'word'], manual.add_skills, manual.remove_skills)]).toEqual(['slides', 'excel']);
  });
  it('connector changes preserve skill choices and expert selection', () => {
    const original = { expert_id: 'developer', add_skills: ['excel'], remove_skills: [], remove_connectors: ['docs'] };
    expect(toggleCapability(original, 'connectors', 'docs', true)).toEqual({ ...original, add_connectors: ['docs'], remove_connectors: [] });
    expect(original.remove_connectors).toEqual(['docs']);
  });
});

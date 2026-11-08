import type { Presentation } from './model';

export interface LocalDraft {
    presentation: Presentation;
    saved: string;
    draftRevision: string;
    pageId: string;
    selected: string;
}

// dot: Keep recovery scoped to the signed-in user, configured target and project,
// never the short-lived Runtime instance. No credentials or result images are saved.
const memory = new Map<string, LocalDraft>();
export function draftKey(user: string, runtime: string, project: string) {
    return `emo-editor-draft:${JSON.stringify([user, runtime, project])}`;
}
export function rememberDraft(key: string, draft: LocalDraft): boolean {
    const dirty = JSON.stringify(draft.presentation) !== draft.saved;
    if (dirty) memory.set(key, structuredClone(draft));
    else memory.delete(key);
    try {
        if (dirty) sessionStorage.setItem(key, JSON.stringify(draft));
        else sessionStorage.removeItem(key);
        return true;
    } catch {
        // Still recoverable after an in-page login, but not a full page reload.
        return false;
    }
}
export function recoverDraft(key: string): LocalDraft | undefined {
    const inMemory = memory.get(key);
    if (inMemory) return structuredClone(inMemory);
    try {
        const value = sessionStorage.getItem(key);
        if (!value) return;
        const draft = JSON.parse(value) as LocalDraft;
        if (typeof draft.saved !== 'string' || typeof draft.draftRevision !== 'string'
            || !draft.presentation || !Array.isArray(draft.presentation.pageOrder)
            || !draft.presentation.pages) return;
        return draft;
    } catch { return; }
}

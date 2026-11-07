import { openDB } from 'idb';

export interface SavedLabel {
  id: string;
  name: string;
  widthMm: number;
  lengthMm: number;
  /** Optional for labels saved before cut markers were introduced. */
  cutPositionsMm?: number[];
  modified: number;
  preview: string;
  design: string;
}

const database = openDB('vc500w-labels', 1, {
  upgrade(db) { db.createObjectStore('labels', { keyPath: 'id' }); }
});

export async function listLabels(): Promise<SavedLabel[]> {
  return (await (await database).getAll('labels')).sort((a, b) => b.modified - a.modified);
}
export async function saveLabel(label: SavedLabel): Promise<void> {
  await (await database).put('labels', label);
}
export async function deleteLabel(id: string): Promise<void> {
  await (await database).delete('labels', id);
}

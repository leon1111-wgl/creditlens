// The gallery page must cite the run that computed its numbers, not the run that selected the filers.
import { describe, expect, test } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { galleryNote, type GalleryIndex } from '../src/snapshot/gallery';
import { DEFINITIONS_VERSION } from '../src/snapshot/model';

const gallery = JSON.parse(readFileSync(resolve(process.cwd(), '../creditlens/snapshot/gallery.json'), 'utf8')) as GalleryIndex;

describe('gallery provenance', () => {
  test('the note cites computed_by.run and its commit for the numbers, and the selection run only as selection', () => {
    const note = galleryNote(gallery);
    expect(note).toContain(`运行 ${gallery.computed_by.run}`);
    expect(note).toContain(gallery.computed_by.commit.slice(0, 7));
    expect(note).toMatch(new RegExp(`选样与取文件[^。]*运行 ${gallery.run}`));
    expect(note.indexOf(gallery.computed_by.run)).toBeLessThan(note.indexOf('打开后'));
  });
  test('the definitions the numbers were computed under are the current ones', () => {
    expect(gallery.computed_by.definitions).toBe(DEFINITIONS_VERSION);
    expect(gallery.definitions).toBe(DEFINITIONS_VERSION);
  });
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {slug} from './slug.js';
test('normalize whitespace and case without dropping numbers', () => {
 assert.equal(slug('  Hello  World  '), 'hello-world');
 assert.equal(slug('Version 2'), 'version-2');
 assert.equal(slug(''), '');
 assert.equal(slug('A\tB\nC'), 'a-b-c');
});

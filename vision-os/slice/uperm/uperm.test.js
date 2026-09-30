/**
 * U-PERM test suite — functional + ADVERSARIAL.
 * Run: node --test uperm.test.js   (Node >= 18, zero dependencies)
 */
'use strict';

const { test, describe } = require('node:test');
const assert = require('node:assert/strict');
const { createAuthority } = require('./uperm.js');

const R = 'file:read';
const W = 'file:write';
const D = 'file:delete';

function fresh() {
  return createAuthority();
}

// ───────────────────────── functional ─────────────────────────

describe('functional: mint / derive / check', () => {
  test('minted capability authorizes its rights on its object', () => {
    const a = fresh();
    const t = a.mint('doc:a', [R, W]);
    assert.equal(a.check(t, 'doc:a', R), true);
    assert.equal(a.check(t, 'doc:a', W), true);
  });

  test('check denies ungranted operations', () => {
    const a = fresh();
    const t = a.mint('doc:a', [R]);
    assert.equal(a.check(t, 'doc:a', W), false);
    assert.equal(a.check(t, 'doc:a', D), false);
  });

  test('check denies wrong object', () => {
    const a = fresh();
    const t = a.mint('doc:a', [R, W, D]);
    assert.equal(a.check(t, 'doc:b', R), false);
  });

  test('derive with diminished rights works', () => {
    const a = fresh();
    const root = a.mint('doc:a', [R, W]);
    const child = a.derive(root, [R]);
    assert.equal(a.check(child, 'doc:a', R), true);
    assert.equal(a.check(child, 'doc:a', W), false);
    // parent unaffected
    assert.equal(a.check(root, 'doc:a', W), true);
  });

  test('multi-level delegation chain', () => {
    const a = fresh();
    const r0 = a.mint('db:main', [R, W, D]);
    const r1 = a.derive(r0, [R, W]);
    const r2 = a.derive(r1, [R]);
    assert.equal(a.check(r2, 'db:main', R), true);
    assert.equal(a.check(r2, 'db:main', W), false);
    const insp = a.inspect(r2);
    assert.equal(insp.depth, 2);
    assert.equal(insp.hasParent, true);
  });

  test('derive with exactly equal rights is allowed', () => {
    const a = fresh();
    const root = a.mint('doc:a', [R]);
    const child = a.derive(root, [R]);
    assert.equal(a.check(child, 'doc:a', R), true);
  });

  test('revoke returns count and kills the token', () => {
    const a = fresh();
    const t = a.mint('doc:a', [R, W]);
    assert.equal(a.revoke(t), 1);
    assert.equal(a.check(t, 'doc:a', R), false);
  });

  test('revocation propagates to all descendants', () => {
    const a = fresh();
    const root = a.mint('doc:a', [R, W, D]);
    const c1 = a.derive(root, [R, W]);
    const c2 = a.derive(c1, [R]);
    const c3 = a.derive(root, [D]);
    const n = a.revoke(root);
    assert.equal(n, 4);
    for (const t of [root, c1, c2, c3]) {
      assert.equal(a.check(t, 'doc:a', R), false, 'revoked subtree must deny');
    }
  });

  test('revoking a mid-tree node kills only its subtree', () => {
    const a = fresh();
    const root = a.mint('doc:a', [R, W]);
    const c1 = a.derive(root, [R, W]);
    const c2 = a.derive(c1, [R]);
    const sib = a.derive(root, [R]);
    assert.equal(a.revoke(c1), 2);
    assert.equal(a.check(c2, 'doc:a', R), false);
    assert.equal(a.check(root, 'doc:a', W), true, 'root survives');
    assert.equal(a.check(sib, 'doc:a', R), true, 'sibling subtree survives');
  });

  test('expiry denies after ttl', async () => {
    const a = fresh();
    const t = a.mint('doc:a', [R], { ttlMs: 30 });
    assert.equal(a.check(t, 'doc:a', R), true);
    await new Promise((r) => setTimeout(r, 60));
    assert.equal(a.check(t, 'doc:a', R), false, 'expired token must deny');
  });

  test('expired parent blocks derive', async () => {
    const a = fresh();
    const p = a.mint('doc:a', [R, W], { ttlMs: 20 });
    await new Promise((r) => setTimeout(r, 50));
    assert.throws(() => a.derive(p, [R]), /revoked or expired/);
  });

  test('authorities are isolated from each other', () => {
    const a1 = fresh();
    const a2 = fresh();
    const t = a1.mint('doc:a', [R]);
    assert.equal(a2.check(t, 'doc:a', R), false, 'foreign token unknown here');
    assert.throws(() => a2.revoke(t), /unknown capability/);
  });

  test('input validation throws TypeError', () => {
    const a = fresh();
    assert.throws(() => a.mint('', [R]), TypeError);
    assert.throws(() => a.mint('x', []), TypeError);
    assert.throws(() => a.mint('x', ['']), TypeError);
    assert.throws(() => a.mint('x', 'file:read'), TypeError);
  });

  test('inspect returns frozen snapshot', () => {
    const a = fresh();
    const t = a.mint('doc:a', [R, W], { label: 'owner' });
    const s = a.inspect(t);
    assert.equal(s.objectId, 'doc:a');
    assert.deepEqual(s.rights, [R, W]);
    assert.equal(s.label, 'owner');
    assert.equal(Object.isFrozen(s), true);
    assert.equal(a.inspect('nope'), null);
  });
});

// ───────────────────────── adversarial ─────────────────────────

describe('adversarial: attacker tries to break the model', () => {
  test('ADV-01 forged token (random 256-bit guess) is rejected', () => {
    const a = fresh();
    a.mint('vault', [R, W, D]); // real token exists; attacker doesn't have it
    const forged = 'f'.repeat(64);
    assert.equal(a.check(forged, 'vault', D), false);
    assert.equal(a.inspect(forged), null);
  });

  test('ADV-02 empty / malformed tokens are rejected, never throw', () => {
    const a = fresh();
    for (const bad of ['', null, undefined, 123, {}, [], 'x'.repeat(63), 'x'.repeat(65)]) {
      assert.equal(a.check(bad, 'doc:a', R), false, `must deny ${String(bad)}`);
    }
  });

  test('ADV-03 rights escalation via derive throws RangeError', () => {
    const a = fresh();
    const parent = a.mint('doc:a', [R]); // read-only parent
    assert.throws(() => a.derive(parent, [R, W]), RangeError);
    assert.throws(() => a.derive(parent, [W]), RangeError);
    assert.throws(() => a.derive(parent, ['admin:*']), RangeError);
    // parent's own rights unchanged after failed attempts
    assert.equal(a.check(parent, 'doc:a', R), true);
    assert.equal(a.check(parent, 'doc:a', W), false);
  });

  test('ADV-04 escalation via derive with duplicate/superset tricks fails', () => {
    const a = fresh();
    const parent = a.mint('doc:a', [R]);
    assert.throws(() => a.derive(parent, [R, R, W]), RangeError);
    assert.throws(() => a.derive(parent, ['FILE:READ']), RangeError, 'case differs = different right');
  });

  test('ADV-05 mutating the inspect() copy cannot change stored rights', () => {
    const a = fresh();
    const t = a.mint('doc:a', [R]);
    const snap = a.inspect(t);
    assert.throws(() => { snap.rights.push(W); }, TypeError, 'frozen array');
    assert.throws(() => { snap.revoked = false; }, TypeError, 'frozen object');
    // Even if an attacker held a mutable reference pattern, stored Set is private:
    assert.equal(a.check(t, 'doc:a', W), false, 'no write right was smuggled in');
  });

  test('ADV-06 revoked capability reuse is denied', () => {
    const a = fresh();
    const t = a.mint('doc:a', [R, W, D]);
    const copyOfTokenString = `${t}`; // bearer copy — same string
    a.revoke(t);
    assert.equal(a.check(copyOfTokenString, 'doc:a', D), false);
    assert.equal(a.check(copyOfTokenString, 'doc:a', R), false);
  });

  test('ADV-07 grandchild of revoked ancestor is denied (propagation depth)', () => {
    const a = fresh();
    const root = a.mint('doc:a', [R, W, D]);
    let cur = root;
    const chain = [root];
    for (let i = 0; i < 5; i++) {
      cur = a.derive(cur, [R, W, D]);
      chain.push(cur);
    }
    a.revoke(chain[1]); // revoke depth-1 node
    assert.equal(a.check(chain[0], 'doc:a', R), true, 'root above revocation still live');
    for (let i = 1; i < chain.length; i++) {
      assert.equal(a.check(chain[i], 'doc:a', R), false, `depth ${i} must be dead`);
    }
  });

  test('ADV-08 derive from a revoked parent is refused', () => {
    const a = fresh();
    const p = a.mint('doc:a', [R, W]);
    a.revoke(p);
    assert.throws(() => a.derive(p, [R]), /revoked or expired/);
  });

  test('ADV-09 confused deputy: deputy must not spend its own authority for a caller', () => {
    const a = fresh();
    // The deputy (e.g. a billing service) holds its OWN write cap on the ledger.
    const deputyCap = a.mint('ledger', [W]);
    // The attacker holds only a read cap on the ledger.
    const attackerCap = a.derive(a.mint('ledger', [R]), [R]);

    // Deputy pattern done RIGHT: it acts only on the capability the caller presents.
    function deputyWrite(callerToken, data) {
      // The deputy checks the CALLER's capability, never its own, for caller requests.
      if (!a.check(callerToken, 'ledger', W)) {
        return { ok: false, reason: 'caller lacks ledger:write' };
      }
      return { ok: true, data };
    }

    const attack = deputyWrite(attackerCap, 'forged-entry');
    assert.equal(attack.ok, false, 'deputy must refuse: caller has no write right');
    assert.match(attack.reason, /lacks/);

    // And the deputy's own cap still works for its legitimate internal use:
    assert.equal(a.check(deputyCap, 'ledger', W), true);
  });

  test('ADV-10 confused deputy via ambient lookup is impossible — no ambient API', () => {
    const a = fresh();
    a.mint('ledger', [W]);
    // There is no function like authority.findCapable('ledger','file:write'):
    assert.equal(typeof a.listTokens, 'undefined');
    assert.equal(typeof a.find, 'undefined');
    assert.equal(typeof a.table, 'undefined');
    assert.equal(Object.keys(a).sort().join(','), 'check,derive,inspect,mint,revoke');
  });

  test('ADV-11 cross-object capability use is denied', () => {
    const a = fresh();
    const t = a.mint('doc:public', [R, W, D]);
    assert.equal(a.check(t, 'doc:secret', R), false);
    assert.equal(a.check(t, 'doc:secret', D), false);
  });

  test('ADV-12 expiry cannot be bypassed by re-deriving after expiry', async () => {
    const a = fresh();
    const p = a.mint('doc:a', [R, W], { ttlMs: 20 });
    const child = a.derive(p, [R, W]); // derived while parent live
    await new Promise((r) => setTimeout(r, 50));
    assert.equal(a.check(p, 'doc:a', R), false);
    assert.equal(a.check(child, 'doc:a', R), false, 'child dies with expired lineage');
    assert.throws(() => a.derive(child, [R]), /revoked or expired/);
  });

  test('ADV-13 double revoke is safe and idempotent in effect', () => {
    const a = fresh();
    const t = a.mint('doc:a', [R]);
    assert.equal(a.revoke(t), 1);
    assert.equal(a.revoke(t), 0, 'second revoke revokes nothing new');
    assert.equal(a.check(t, 'doc:a', R), false);
  });

  test('ADV-14 token uniqueness: 10k mints, no collisions', () => {
    const a = fresh();
    const seen = new Set();
    for (let i = 0; i < 10000; i++) {
      const t = a.mint('obj', [R]);
      assert(!seen.has(t), 'token collision — catastrophic');
      seen.add(t);
      assert.equal(t.length, 64, '256-bit hex');
    }
  });

  test('ADV-15 check() with non-string object/operation never throws, denies', () => {
    const a = fresh();
    const t = a.mint('doc:a', [R]);
    assert.equal(a.check(t, null, R), false);
    assert.equal(a.check(t, 'doc:a', null), false);
    assert.equal(a.check(t, undefined, undefined), false);
  });
});

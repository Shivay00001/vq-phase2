/**
 * U-PERM — capability-based permission core for VisionOS.
 *
 * Object-capability model (seL4/Zircon lineage):
 *  - A capability is an unguessable bearer token naming ONE object plus the
 *    exact operations allowed on it. No ambient authority: every privileged
 *    action requires presenting a token.
 *  - Capabilities form a derivation tree. A child may only hold rights that
 *    are a subset of its parent's rights (minting an escalation throws).
 *  - Revoking a capability revokes its entire descendant subtree.
 *
 * The authority table lives in a closure. Tokens are 256-bit random hex
 * strings; there is no API to enumerate them. `inspect()` returns frozen
 * deep copies so callers can never mutate stored grants.
 *
 * Bearer-token warning: whoever holds the token string holds the right.
 * Transport/storage protection is the caller's responsibility.
 *
 * Zero dependencies. Works in Node (>=18) and browsers.
 * (In browsers, replace the node:crypto import with globalThis.crypto.)
 */
'use strict';

const crypto = (() => {
  try {
    // Node.js
    // eslint-disable-next-line global-require
    return require('node:crypto');
  } catch {
    // Browser: WebCrypto
    if (globalThis.crypto && globalThis.crypto.getRandomValues) {
      return {
        randomBytes: (n) => {
          const b = new Uint8Array(n);
          globalThis.crypto.getRandomValues(b);
          return { toString: () => [...b].map((x) => x.toString(16).padStart(2, '0')).join('') };
        },
      };
    }
    throw new Error('U-PERM: no secure RNG available');
  }
})();

function newToken() {
  return crypto.randomBytes(32).toString('hex'); // 256-bit
}

function assertValidRights(rights, what) {
  if (!Array.isArray(rights) || rights.length === 0) {
    throw new TypeError(`${what}: rights must be a non-empty array of strings`);
  }
  for (const r of rights) {
    if (typeof r !== 'string' || r.length === 0) {
      throw new TypeError(`${what}: each right must be a non-empty string`);
    }
  }
}

function assertValidObjectId(objectId, what) {
  if (typeof objectId !== 'string' || objectId.length === 0) {
    throw new TypeError(`${what}: objectId must be a non-empty string`);
  }
}

function nowMs() {
  return Date.now();
}

function createAuthority() {
  // token -> record. NEVER exposed; no enumeration API exists by design.
  const table = new Map();

  function get(token) {
    return typeof token === 'string' ? table.get(token) || null : null;
  }

  function isLive(rec) {
    if (!rec || rec.revoked) return false;
    if (rec.expiresAt !== null && nowMs() >= rec.expiresAt) return false;
    return true;
  }

  // Walk up the derivation tree; false if ANY ancestor (or self) is revoked.
  function lineageLive(rec) {
    let cur = rec;
    const seen = new Set();
    while (cur) {
      if (seen.has(cur.token)) return false; // cycle guard (should be impossible)
      seen.add(cur.token);
      if (cur.revoked) return false;
      if (cur.expiresAt !== null && nowMs() >= cur.expiresAt) return false;
      cur = cur.parentToken ? table.get(cur.parentToken) || null : null;
    }
    return true;
  }

  function resolveExpiry(opts) {
    if (!opts) return null;
    if (opts.expiresAt !== undefined && opts.expiresAt !== null) {
      if (typeof opts.expiresAt !== 'number' || !Number.isFinite(opts.expiresAt)) {
        throw new TypeError('expiresAt must be a finite number (epoch ms)');
      }
      return opts.expiresAt;
    }
    if (opts.ttlMs !== undefined && opts.ttlMs !== null) {
      if (typeof opts.ttlMs !== 'number' || !Number.isFinite(opts.ttlMs) || opts.ttlMs <= 0) {
        throw new TypeError('ttlMs must be a positive finite number');
      }
      return nowMs() + opts.ttlMs;
    }
    return null;
  }

  function store(objectId, rights, parentToken, opts) {
    const token = newToken();
    // Practically impossible collision, but never silently overwrite.
    if (table.has(token)) throw new Error('U-PERM: token collision — retry');
    const rec = {
      token,
      objectId,
      rights: new Set(rights),
      parentToken,
      children: new Set(),
      revoked: false,
      expiresAt: resolveExpiry(opts),
      label: (opts && typeof opts.label === 'string') ? opts.label : '',
      depth: 0,
    };
    if (parentToken) {
      const parent = table.get(parentToken);
      parent.children.add(token);
      rec.depth = parent.depth + 1;
    }
    table.set(token, rec);
    return token;
  }

  const authority = {
    /**
     * Mint a root capability for an object.
     * @returns {string} bearer token
     */
    mint(objectId, rights, opts) {
      assertValidObjectId(objectId, 'mint');
      assertValidRights(rights, 'mint');
      return store(objectId, rights, null, opts);
    },

    /**
     * Derive a child capability with rights ⊆ parent rights.
     * Throws RangeError on escalation attempt; throws Error on
     * revoked/expired/unknown parent.
     * @returns {string} bearer token
     */
    derive(parentToken, rights, opts) {
      assertValidRights(rights, 'derive');
      const parent = get(parentToken);
      if (!parent) throw new Error('derive: unknown parent capability');
      if (!lineageLive(parent)) {
        throw new Error('derive: parent capability is revoked or expired');
      }
      for (const r of rights) {
        if (!parent.rights.has(r)) {
          throw new RangeError(
            `derive: escalation denied — right "${r}" not held by parent` +
            (parent.label ? ` (${parent.label})` : '')
          );
        }
      }
      return store(parent.objectId, rights, parentToken, opts);
    },

    /**
     * Check authority. Deny-by-default: never throws on the deny path,
     * returns false for unknown/expired/revoked/mismatched tokens.
     */
    check(token, objectId, operation) {
      const rec = get(token);
      if (!rec) return false;
      if (typeof objectId !== 'string' || typeof operation !== 'string') return false;
      if (rec.objectId !== objectId) return false;
      if (!rec.rights.has(operation)) return false;
      if (!lineageLive(rec)) return false;
      return true;
    },

    /**
     * Revoke a capability and ALL transitive descendants.
     * @returns {number} count of capabilities revoked
     */
    revoke(token) {
      const rec = get(token);
      if (!rec) throw new Error('revoke: unknown capability');
      let count = 0;
      const stack = [rec];
      const seen = new Set();
      while (stack.length) {
        const cur = stack.pop();
        if (seen.has(cur.token)) continue;
        seen.add(cur.token);
        if (!cur.revoked) {
          cur.revoked = true;
          count += 1;
        }
        for (const childToken of cur.children) {
          const child = table.get(childToken);
          if (child) stack.push(child);
        }
      }
      return count;
    },

    /**
     * Introspection for debugging/auditing. Returns a FROZEN deep copy —
     * mutating the result cannot alter stored grants. Null for unknown token.
     * There is intentionally no API to list all tokens.
     */
    inspect(token) {
      const rec = get(token);
      if (!rec) return null;
      return Object.freeze({
        objectId: rec.objectId,
        rights: Object.freeze([...rec.rights].sort()),
        label: rec.label,
        expiresAt: rec.expiresAt,
        revoked: rec.revoked,
        depth: rec.depth,
        hasParent: rec.parentToken !== null,
        childCount: rec.children.size,
      });
    },
  };

  return Object.freeze(authority);
}

module.exports = { createAuthority };

// Browser global (optional): window.UPERM = { createAuthority }
if (typeof globalThis !== 'undefined' && typeof window !== 'undefined') {
  window.UPERM = { createAuthority };
}

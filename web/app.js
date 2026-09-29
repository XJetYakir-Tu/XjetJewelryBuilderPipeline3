// Pipeline 3 customer UI (Alpine.js).
//
// Rules this file follows (spec sections 4 and 10):
//   * the server is authoritative for batches, selection, quotes and the bag;
//   * every async response is checked against the design/batch it was requested
//     for, so late results never land in a different design;
//   * reload only READS state — it never re-submits paid generation work.

const STORE_KEY = 'p3_state';

function loadStore() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || '{}'); } catch { return {}; }
}
function saveStore(obj) {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(obj)); } catch { /* storage unavailable */ }
}
function newRequestId() {
  return (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random().toString(16).slice(2);
}

class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

function p3App() {
  return {
    // session
    token: '', tokenInput: '', session: null, authError: '',
    view: 'design',                 // design | customize | bag | designs
    catalog: null,

    // compose
    prompt: '', referenceFile: null, referencePreview: null, rightsConfirmed: false,
    composeError: '', submitting: false,

    // current design
    design: null,                   // full design state from the server
    viewBatchId: null,              // batch shown in the grid
    pendingRefineBatchId: null,     // refinement in progress (shown as progress, not yet in view)
    refineOpen: false, refineText: '', refineError: '',
    actionError: '',
    lightbox: null, lightboxBig: false,

    // customize
    cust: null, custError: '', mediaTab: 'image', lastMaterialByGroup: { fashion: 'silver', luxury: null },
    quotePending: false, bagMessage: '',

    // bag / designs
    bag: null, designs: [],

    _poll: null, _pollCust: null,

    // ── lifecycle ─────────────────────────────────────────────────────
    async init() {
      const st = loadStore();
      this.catalog = await this.api('GET', '/api/catalog', null, { noAuth: true });
      const lux = this.materialsOf('luxury');
      this.lastMaterialByGroup.luxury = lux.length ? lux[0].id : null;
      if (st.token) {
        this.token = st.token;
        try { this.session = await this.api('GET', '/api/session'); } catch { this.token = ''; }
      }
      if (!this.token) return;
      this.refreshBag();
      if (st.designId) {
        try {
          await this.openDesign(st.designId, { restoreView: st.view });
        } catch { this.persist({ designId: null }); }
      }
    },

    persist(patch) { saveStore({ ...loadStore(), ...patch }); },

    async api(method, path, body, opts = {}) {
      const headers = {};
      if (!opts.noAuth && this.token) headers['X-Access-Token'] = this.token;
      let payload;
      if (body instanceof FormData) payload = body;
      else if (body !== null && body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
      let resp;
      try {
        resp = await fetch(path, { method, headers, body: payload });
      } catch (e) {
        throw new ApiError(0, 'network', 'Network error — please check your connection.');
      }
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const err = data.error || {};
        if (resp.status === 401 && !opts.noAuth) { this.signOut(); }
        throw new ApiError(resp.status, err.code || 'error', err.message || `Request failed (${resp.status})`);
      }
      return data;
    },

    async signIn() {
      this.authError = '';
      this.token = this.tokenInput.trim();
      try {
        this.session = await this.api('GET', '/api/session', null);
        this.persist({ token: this.token });
        this.refreshBag();
      } catch (e) {
        this.token = ''; this.authError = e.message;
      }
    },
    signOut() {
      this.token = ''; this.session = null; this.design = null; this.cust = null;
      saveStore({});
    },

    // ── catalog helpers ───────────────────────────────────────────────
    group(id) { return this.catalog?.groups.find(g => g.id === id); },
    materialsOf(groupId) { return this.group(groupId)?.materials || []; },
    material(id) {
      for (const g of this.catalog?.groups || []) for (const m of g.materials) if (m.id === id) return m;
      return null;
    },
    groupOfMaterial(id) { return this.material(id)?.group; },

    // ── compose / new design ──────────────────────────────────────────
    pickReference(ev) {
      const f = ev.target.files[0];
      this.referenceFile = f || null;
      this.referencePreview = f ? URL.createObjectURL(f) : null;
    },
    clearReference() { this.referenceFile = null; this.referencePreview = null; this.rightsConfirmed = false; },

    async generate() {
      this.composeError = '';
      if (this.prompt.trim().length < 3) { this.composeError = 'Please describe your ring in a few words.'; return; }
      if (this.referenceFile && !this.rightsConfirmed) { this.composeError = 'Please confirm you have the rights to use the uploaded image.'; return; }
      const fd = new FormData();
      fd.append('prompt', this.prompt.trim());
      fd.append('client_request_id', newRequestId());
      if (this.referenceFile) { fd.append('reference', this.referenceFile); fd.append('rights_confirmed', 'true'); }
      this.submitting = true;
      try {
        const batch = await this.api('POST', '/api/designs', fd);
        await this.openDesign(batch.design_id);
      } catch (e) {
        this.composeError = e.message;          // prompt and reference are kept for retry
      } finally {
        this.submitting = false;
      }
    },

    startNew() {
      // Clears the active selection/associations only. Saved designs and the bag are untouched.
      this.stopPolling();
      this.design = null; this.cust = null; this.viewBatchId = null; this.pendingRefineBatchId = null;
      this.refineOpen = false; this.refineText = ''; this.actionError = ''; this.lightbox = null;
      this.prompt = ''; this.clearReference();
      this.view = 'design';
      this.persist({ designId: null, view: 'design' });
    },

    // ── design state ──────────────────────────────────────────────────
    async openDesign(designId, { restoreView } = {}) {
      this.stopPolling();
      const d = await this.api('GET', `/api/designs/${designId}`);
      this.design = d; this.cust = null; this.pendingRefineBatchId = null;
      this.refineOpen = false; this.actionError = '';
      const last = d.batches[d.batches.length - 1];
      // Show the newest batch that has something to show; a still-running refinement is shown as progress.
      const shown = [...d.batches].reverse().find(b => b.status !== 'queued' && b.status !== 'generating') || last;
      this.viewBatchId = shown.id;
      if (last.id !== shown.id) this.pendingRefineBatchId = last.id;
      this.view = 'design';
      this.persist({ designId, view: 'design' });
      if (restoreView === 'customize' && d.customization) {
        this.showCustomization(d.customization);
      }
      this.ensurePolling();
    },

    batch(id) { return this.design?.batches.find(b => b.id === id); },
    get viewBatch() { return this.batch(this.viewBatchId); },
    get pendingBatch() { return this.batch(this.pendingRefineBatchId); },
    get selectedId() { return this.design?.selected_candidate_id || null; },
    get selectedCandidate() {
      for (const b of this.design?.batches || []) for (const c of b.candidates) if (c.id === this.selectedId) return c;
      return null;
    },
    get canActOnSelection() { return this.selectedCandidate?.status === 'ready'; },
    readyCount(b) { return b ? b.candidates.filter(c => c.status === 'ready').length : 0; },
    batchLabel(b, i) { return b.kind === 'initial' ? 'Original' : `Refinement ${i}`; },

    anyActive(b) { return b && (b.status === 'queued' || b.status === 'generating'); },

    ensurePolling() {
      if (this._poll) return;
      const designId = this.design?.id;
      const tick = async () => {
        if (!this.design || this.design.id !== designId) { this.stopPolling(); return; }
        const active = this.design.batches.filter(b => this.anyActive(b));
        if (!active.length) { this.stopPolling(); return; }
        for (const b of active) {
          try {
            const fresh = await this.api('GET', `/api/batches/${b.id}`);
            // Stale-result guard: only apply to the design this poll belongs to.
            if (!this.design || this.design.id !== designId || fresh.design_id !== designId) return;
            const idx = this.design.batches.findIndex(x => x.id === fresh.id);
            if (idx >= 0) this.design.batches.splice(idx, 1, fresh);
            this.onBatchUpdated(fresh);
          } catch (e) { /* transient: next tick retries the same read */ }
        }
      };
      this._poll = setInterval(tick, 1500);
      tick();
    },
    stopPolling() { if (this._poll) { clearInterval(this._poll); this._poll = null; } },

    onBatchUpdated(b) {
      if (b.id === this.pendingRefineBatchId && !this.anyActive(b)) {
        this.pendingRefineBatchId = null;
        if (b.status === 'failed') {
          this.refineError = 'The refinement could not be generated. You can retry the failed images.';
        }
        // Switch to the new batch so the user can choose again; the earlier batch stays in history.
        this.viewBatchId = b.id;
        this.setSelection(null);
      }
    },

    async select(c) {
      if (c.status !== 'ready') return;
      await this.setSelection(c.id);
    },
    async setSelection(candidateId) {
      if (!this.design) return;
      const designId = this.design.id;
      const prev = this.design.selected_candidate_id;
      this.design.selected_candidate_id = candidateId;          // optimistic
      try {
        await this.api('PUT', `/api/designs/${designId}/selection`, { candidate_id: candidateId });
      } catch (e) {
        if (this.design?.id === designId) { this.design.selected_candidate_id = prev; this.actionError = e.message; }
      }
    },

    openZoom(c) { this.lightbox = c; this.lightboxBig = false; },
    closeZoom() { this.lightbox = null; },

    async retrySlot(c) {
      this.actionError = '';
      try {
        const b = await this.api('POST', `/api/candidates/${c.id}/retry`);
        const idx = this.design.batches.findIndex(x => x.id === b.id);
        if (idx >= 0) this.design.batches.splice(idx, 1, b);
        this.ensurePolling();
      } catch (e) { this.actionError = e.message; }
    },
    async retryBatch(b) {
      try {
        const fresh = await this.api('POST', `/api/batches/${b.id}/retry-failed`);
        const idx = this.design.batches.findIndex(x => x.id === fresh.id);
        if (idx >= 0) this.design.batches.splice(idx, 1, fresh);
        this.refineError = '';
        this.ensurePolling();
      } catch (e) { this.actionError = e.message; }
    },

    // ── refine ────────────────────────────────────────────────────────
    async refine() {
      this.refineError = '';
      if (!this.canActOnSelection) return;
      if (this.refineText.trim().length < 3) { this.refineError = 'Describe the change you want.'; return; }
      const designId = this.design.id;
      try {
        const b = await this.api('POST', `/api/designs/${designId}/batches`, {
          parent_candidate_id: this.selectedId, instruction: this.refineText.trim(),
          client_request_id: newRequestId(),
        });
        if (this.design?.id !== designId) return;
        this.design.batches.push(b);
        this.pendingRefineBatchId = b.id;      // keep the current grid until the new batch is ready
        this.refineOpen = false; this.refineText = '';
        this.ensurePolling();
      } catch (e) {
        this.refineError = e.message;          // e.g. reference_unavailable — no text-only fallback
      }
    },

    // ── customize ─────────────────────────────────────────────────────
    async proceed() {
      if (!this.canActOnSelection) return;
      this.actionError = '';
      const designId = this.design.id, candidateId = this.selectedId;
      // Show the selected image immediately; the server returns the authoritative customization.
      this.cust = { candidate_id: candidateId, image_url: this.selectedCandidate.image_url, material_id: 'silver',
                    quote: null, movie: { status: 'queued' }, ring_size: null, quantity: 1, _optimistic: true };
      this.view = 'customize'; this.mediaTab = 'image'; this.custError = ''; this.bagMessage = '';
      try {
        const c = await this.api('POST', `/api/designs/${designId}/customize`, { candidate_id: candidateId });
        if (this.design?.id !== designId || this.cust?.candidate_id !== candidateId) return;
        this.showCustomization(c);
      } catch (e) {
        this.custError = e.message;
      }
    },

    showCustomization(c) {
      this.cust = c;
      this.view = 'customize';
      const g = this.groupOfMaterial(c.material_id);
      if (g) this.lastMaterialByGroup[g] = c.material_id;
      this.persist({ view: 'customize' });
      this.pollCustomization();
    },

    pollCustomization() {
      if (this._pollCust) clearInterval(this._pollCust);
      const custId = this.cust?.id;
      const tick = async () => {
        if (!this.cust || this.cust.id !== custId || this.view !== 'customize') { clearInterval(this._pollCust); this._pollCust = null; return; }
        const st = this.cust.movie?.status;
        if (st !== 'queued' && st !== 'running') { clearInterval(this._pollCust); this._pollCust = null; return; }
        try {
          const fresh = await this.api('GET', `/api/customizations/${custId}`);
          if (this.cust?.id === custId) {
            const becameReady = fresh.movie?.status === 'ready' && this.cust.movie?.status !== 'ready';
            this.cust.movie = fresh.movie;
            if (becameReady) this.mediaTab = 'movie';
          }
        } catch { /* retry next tick */ }
      };
      this._pollCust = setInterval(tick, 2000);
    },

    async retryMovie() {
      if (!this.cust) return;
      const custId = this.cust.id;
      try {
        const m = await this.api('POST', `/api/candidates/${this.cust.candidate_id}/movie`);
        if (this.cust?.id === custId) { this.cust.movie = m; this.pollCustomization(); }
      } catch (e) { this.custError = e.message; }
    },

    get currentGroup() { return this.groupOfMaterial(this.cust?.material_id) || 'fashion'; },

    chooseGroup(groupId) {
      const target = this.lastMaterialByGroup[groupId] || this.materialsOf(groupId)[0]?.id;
      if (target) this.chooseMaterial(target);
    },

    async chooseMaterial(materialId) {
      if (!this.cust?.id || this.cust.material_id === materialId) return;
      const custId = this.cust.id;
      const g = this.groupOfMaterial(materialId);
      this.lastMaterialByGroup[g] = materialId;
      // Never leave a previous fashion price visible while the new quote loads.
      this.cust.material_id = materialId;
      this.cust.quote = null; this.cust.line_total = null; this.cust.can_add_to_bag = false;
      this.quotePending = true; this.bagMessage = '';
      await this.patchCustomization(custId, { material_id: materialId });
    },

    async setSize(v) {
      if (!this.cust?.id) return;
      await this.patchCustomization(this.cust.id, { ring_size: v === '' ? null : Number(v) });
    },
    async setQuantity(delta) {
      if (!this.cust?.id) return;
      const q = Math.min(10, Math.max(1, (this.cust.quantity || 1) + delta));
      if (q !== this.cust.quantity) await this.patchCustomization(this.cust.id, { quantity: q });
    },

    async patchCustomization(custId, patch) {
      try {
        const fresh = await this.api('PATCH', `/api/customizations/${custId}`, patch);
        if (this.cust?.id !== custId) return;
        // Ignore responses for a material the user has already moved away from.
        if (patch.material_id && this.cust.material_id !== patch.material_id) return;
        const movie = this.cust.movie;
        this.cust = { ...fresh, movie: fresh.movie || movie };
      } catch (e) {
        this.custError = e.message;
      } finally {
        this.quotePending = false;
      }
    },

    get isLuxury() { return this.currentGroup === 'luxury'; },
    get priceText() {
      const q = this.cust?.quote;
      if (this.isLuxury) return 'Price unavailable';
      if (!q) return '…';
      if (q.pricing_status !== 'available') return 'Price unavailable';
      return this.money(q.unit_price, q.currency);
    },
    money(v, cur = 'USD') {
      return new Intl.NumberFormat('en-US', { style: 'currency', currency: cur }).format(v);
    },
    get bagBlockedText() {
      if (!this.cust) return '';
      switch (this.cust.add_to_bag_blocked_reason) {
        case 'luxury_preview_only': return 'Luxury pieces are preview-only for now.';
        case 'price_unavailable': return 'Price unavailable — this piece cannot be added to the bag yet.';
        case 'ring_size_required': return 'Choose your ring size to continue.';
        default: return '';
      }
    },

    async addToBag() {
      if (!this.cust?.can_add_to_bag) return;
      this.bagMessage = '';
      try {
        this.bag = await this.api('POST', '/api/bag', { customization_id: this.cust.id });
        this.bagMessage = 'Added to your bag.';
      } catch (e) { this.custError = e.message; }
    },

    backToDesign() {
      this.view = 'design';
      this.persist({ view: 'design' });
    },

    // ── bag / designs ─────────────────────────────────────────────────
    get bagCount() { return (this.bag?.lines || []).reduce((n, l) => n + l.quantity, 0); },
    async refreshBag() { try { this.bag = await this.api('GET', '/api/bag'); } catch { /* ignore */ } },
    async removeLine(l) { try { this.bag = await this.api('DELETE', `/api/bag/${l.id}`); } catch (e) { alert(e.message); } },
    async showBag() { this.view = 'bag'; await this.refreshBag(); },
    async showDesigns() {
      this.view = 'designs';
      try { this.designs = (await this.api('GET', '/api/designs')).designs; } catch { this.designs = []; }
    },
    goDesign() { this.view = 'design'; this.persist({ view: 'design' }); },
  };
}

window.p3App = p3App;

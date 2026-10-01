// Pipeline 3 customer UI (Alpine.js) — P2 look and feel, P3 logic.
//
// Rules this file follows (spec sections 4 and 10):
//   * the server is authoritative for batches, selection, quotes and the bag;
//   * every async response is checked against the design/batch it was requested
//     for, so late results never land in a different design;
//   * reload only READS state — it never re-submits paid generation work.

// Base path injected by the server (<meta name="p3-base">), e.g. "/JewelryB2C3" or "" at the root.
// EVERY request goes through url() so nothing ever escapes to root /api, /static or /assets.
const BASE = (document.querySelector('meta[name="p3-base"]')?.content || '').replace(/\/+$/, '');
const url = (path) => BASE + path;
const STORE_KEY = 'p3_state' + (BASE ? ':' + BASE : '');
// P2 keeps its session in 'xjet_session' / 'xjet_profile'. P3 shares the proto origin with P2 but
// has its own token store, so it uses its own keys and never reads or overwrites P2's session.
const SESSION_KEY = 'p3_session' + (BASE ? ':' + BASE : '');
const PROFILE_KEY = 'p3_profile' + (BASE ? ':' + BASE : '');
const STUDIO_VIEWS = ['ai-studio', 'review', 'checkout'];
const PAGE_VIEWS = ['home', 'inspiration', 'materials', 'technology', 'faq', 'designers',
                    'terms', 'privacy', 'shipping-returns', 'contact'];

// Customer copy per material id (from P2's metals[] descriptions).
const MATERIAL_COPY = {
  stainless_steel: { sub: 'Durable brushed steel', desc: 'Durable, hypoallergenic stainless steel with a cool brushed finish. Everyday strength at an accessible price.' },
  silver:          { sub: 'Sterling Silver 925', desc: 'Classic 92.5% pure silver, digitally jetted for a crisp mirror finish and exceptional detail resolution.' },
  vermeil:         { sub: 'Sterling silver · thick 14K gold plating', desc: 'Sterling silver core with thick 14K gold plating. Luxury look and feel at an accessible price point.' },
  gold_10k_yellow: { desc: '41.7% pure gold — hardest gold alloy, ideal for everyday fine jewelry.' },
  gold_10k_rose:   { desc: 'Warm blush tone with 41.7% gold content — the most durable gold colour.' },
  gold_14k_yellow: { desc: '58.3% pure gold — perfect balance of purity and strength for fine jewellery.' },
  gold_14k_rose:   { desc: 'A warm blush alloy of gold and copper delivering a romantic tone that flatters every skin tone.' },
  gold_18k_yellow: { desc: '75% pure gold — a rich, warm yellow prized for heirloom fine jewellery.' },
  gold_18k_rose:   { desc: 'A romantic blush alloy at 75% gold content — warm, refined, and timeless.' },
};

// Waiting-screen copy from Pipeline 2 (app-p2.js showLoading presets 'design' / 'refine').
const WAIT_PRESETS = {
  design: {
    title: 'Your jewelry is coming to life',
    message: 'Turning your vision into a detailed design, ready for precision 3D printing.',
    statuses: ['Creating your design...', 'Interpreting your idea...', 'Developing the jewelry geometry...', 'Refining the design details...', 'Preparing your result...'],
  },
  refine: {
    title: 'Refining your jewelry design',
    message: 'Applying your ideas while preserving the character of your creation.',
    statuses: ['Refining your design...', 'Interpreting your changes...', 'Updating the jewelry geometry...', 'Refining the details...', 'Preparing your result...'],
  },
};

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
    SUPPORT_EMAIL: 'atelier@xjet3d.com',   // carried over from P2 (marked "TODO confirm" there)

    // ── app / session ────────────────────────────────────────────────
    view: 'home',
    health: null,
    catalog: null,

    // ── Session / token accounting (P2 app-p2.js) ─────────────────────
    userSession: null,                 // {token, quotaUsed, quotaMax, name, email}
    userProfile: { name: '', email: '' },
    signInNotice: null,                // {title, text} banner on the Design screen after sign-in
    showRegModal: false,
    regMode: 'email',                  // 'email' = self-registration, 'token' = enter existing token
    regForm: { token: '', name: '', email: '' },
    regError: '', regInfo: '', regLoading: false,
    quotaUsed: 0, quotaMax: 10,
    get quotaRemaining() { return Math.max(0, this.quotaMax - this.quotaUsed); },
    get token() { return (this.userSession && this.userSession.token) || ''; },
    get displayName() {
      if (!this.userSession) return '';
      return String((this.userProfile && this.userProfile.name) || this.userSession.name || '').trim();
    },
    get displayEmail() {
      if (!this.userSession) return '';
      return String((this.userProfile && this.userProfile.email) || this.userSession.email || '').trim();
    },
    accountPanelOpen: false, tokenCopied: false,

    // ── studio: compose ──────────────────────────────────────────────
    userInput: '', uploadedFile: null, uploadedPreview: null, rightsConfirmed: false,
    composeError: '', submitting: false,

    // ── studio: current design ───────────────────────────────────────
    design: null,
    viewBatchId: null,
    pendingRefineBatchId: null,
    actionError: '',
    sidebarOpen: window.innerWidth >= 1024,
    designs: [], designsLoading: false, designsError: '', projectSearch: '',

    // ── preview overlay (P2 fullscreen zoom) ─────────────────────────
    previewOpen: false, previewMedia: 'image', previewSrc: null, previewCandidate: null,
    previewZoom: 1, previewPanX: 0, previewPanY: 0, _panning: false, _panStart: null,

    // ── customize ────────────────────────────────────────────────────
    cust: null, custError: '', mediaTab: 'movie', quotePending: false, bagMessage: '',
    lastMaterialByGroup: { fashion: 'silver', luxury: null },
    groupOpen: { fashion: true, luxury: false },
    showSizeGuide: false,

    // ── bag ──────────────────────────────────────────────────────────
    bag: null,

    // ── developer AI-mode control (internal; needs P3_ADMIN_KEY) ──────
    devKey: '', devKeyInput: '', devPromptOpen: false, devError: '', devMode: null,
    liveConfirmOpen: false, liveConfirmText: '', devBusy: false,

    _poll: null, _pollCust: null,
    waitStatusIndex: 0,

    // ── lifecycle ─────────────────────────────────────────────────────
    async init() {
      const st = loadStore();
      // Rotate the waiting-screen status line every 3.5 s (P2 cadence); skipped for reduced motion.
      if (!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) {
        setInterval(() => { this.waitStatusIndex++; }, 3500);
      }
      try { this.health = await this.api('GET', '/api/health', null, { noAuth: true }); } catch { this.health = null; }
      this.catalog = await this.api('GET', '/api/catalog', null, { noAuth: true });
      const lux = this.materialsOf('luxury');
      this.lastMaterialByGroup.luxury = lux.length ? lux[0].id : null;
      try {
        const P = JSON.parse(localStorage.getItem(PROFILE_KEY) || 'null');
        if (P) this.userProfile = { name: P.name || '', email: P.email || '' };
      } catch (_) {}
      // A #token=… link (verification email / verify page) signs the user straight in and takes
      // precedence over a saved session; otherwise restore the saved session (P2 behaviour).
      const fromLink = this._loginFromUrlToken();
      if (!fromLink) {
        try {
          const S = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null');
          if (S && S.token) {
            this.userSession = S;
            this.quotaUsed = S.quotaUsed || 0;
            this.quotaMax = S.quotaMax || 10;
            if (S.name && !this.userProfile.name) this._saveUserProfile(S.name, null);
          }
        } catch (_) { try { localStorage.removeItem(SESSION_KEY); } catch (__) {} }
      }
      if (this.userSession) await this._refreshQuota();
      try { this.devKey = sessionStorage.getItem('p3_dev_key') || ''; } catch { this.devKey = ''; }
      if (this.devKey) this.loadDevMode();
      const hashView = (location.hash || '').replace('#', '');
      if (PAGE_VIEWS.includes(hashView)) this.view = hashView;
      if (!this.token) return;
      this.refreshBag();
      if (fromLink) { this._enterDesignAfterSignIn(true); return; }
      if (st.designId && STUDIO_VIEWS.includes(st.view)) {
        try { await this.openDesign(st.designId, { restoreView: st.view }); }
        catch { this.persist({ designId: null }); }
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
        resp = await fetch(url(path), { method, headers, body: payload });
      } catch (e) {
        throw new ApiError(0, 'network', 'Network error — please check your connection.');
      }
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const err = data.error || {};
        if (resp.status === 402) this._refreshQuota();
        throw new ApiError(resp.status, err.code || 'error', err.message || `Request failed (${resp.status})`);
      }
      return data;
    },

    // ── mode (mock vs live) ───────────────────────────────────────────
    get isMock() { return this.health?.mode === 'mock'; },
    get isLive() { return this.health?.mode === 'live'; },

    // ── developer AI-mode control ──────────────────────────────────────
    async devRequest(method, path, body) {
      const resp = await fetch(url(path), { method, headers: { 'Authorization': 'Bearer ' + this.devKey,
        ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new ApiError(resp.status, data.error?.code || 'error', data.error?.message || ('HTTP ' + resp.status));
      return data;
    },
    async loadDevMode() {
      try { this.devMode = await this.devRequest('GET', '/api/dev/mode'); }
      catch (e) { this.devMode = null; if (e.status === 403 || e.status === 503) this.forgetDevKey(); }
    },
    async devConnect() {
      this.devError = ''; this.devKey = this.devKeyInput.trim();
      try {
        this.devMode = await this.devRequest('GET', '/api/dev/mode');
        try { sessionStorage.setItem('p3_dev_key', this.devKey); } catch {}
        this.devPromptOpen = false; this.devKeyInput = '';
      } catch (e) { this.devError = e.message; this.devKey = ''; }
    },
    forgetDevKey() { this.devKey = ''; this.devMode = null; try { sessionStorage.removeItem('p3_dev_key'); } catch {} },
    async switchAiMode(target) {
      this.devError = ''; this.devBusy = true;
      try {
        const body = { mode: target };
        if (target === 'live') body.confirmation = this.liveConfirmText.trim();
        this.devMode = await this.devRequest('POST', '/api/dev/mode', body);
        this.liveConfirmOpen = false; this.liveConfirmText = '';
        try { this.health = await this.api('GET', '/api/health', null, { noAuth: true }); } catch {}
      } catch (e) { this.devError = e.message; }
      finally { this.devBusy = false; }
    },

    // ── navigation ────────────────────────────────────────────────────
    navigateTo(v) {
      if (this.previewOpen) this.closePreview();
      this.view = v;
      if (PAGE_VIEWS.includes(v)) history.replaceState(null, '', v === 'home' ? location.pathname : '#' + v);
      else history.replaceState(null, '', location.pathname);
      if (STUDIO_VIEWS.includes(v)) this.persist({ view: v });
      window.scrollTo({ top: 0 });
      document.querySelector('main')?.scrollTo?.({ top: 0 });
      if (v === 'checkout') this.refreshBag();
    },
    scrollToHowItWorks() {
      this.navigateTo('home');
      this.$nextTick(() => setTimeout(() => document.getElementById('how-it-works')?.scrollIntoView({ behavior: 'smooth' }), 80));
    },

    // Start Designing (P2 resetAIFlow): sign in if needed, then open a fresh studio.
    resetAIFlow() {
      if (!this.userSession) { this.openRegModal(); return; }
      this._doResetAIFlow();
    },
    _doResetAIFlow() {
      this.startNew();
      this.navigateTo('ai-studio');
      this._refreshQuota();             // authoritative count every time the Design screen opens
    },

    // ── Sign-in / registration (P2 JewelryB2C2) ───────────────────────
    openRegModal(mode = 'email') {
      this.regMode = mode;
      this.regError = ''; this.regInfo = '';
      this.regForm = { token: '', name: '', email: '' };
      this.showRegModal = true;
    },
    async submitEmailRegistration() {
      this.regLoading = true; this.regError = ''; this.regInfo = '';
      try {
        const Name = this.regForm.name.trim(), Email = this.regForm.email.trim();
        const Result = await this.api('POST', '/api/register', { Name, Email }, { noAuth: true });
        this._saveUserProfile(Name, Email);
        // The token is never returned here — it is emailed. Show a confirmation message.
        this.regInfo = Result.message
          || "We've sent a verification email to your inbox. Please verify your email to save your designs and continue creating your jewelry.";
      } catch (E) {
        this.regError = E.message;
      } finally {
        this.regLoading = false;
      }
    },
    async submitRegistration() {
      this.regLoading = true; this.regError = '';
      try {
        const Token = this.regForm.token.toUpperCase().trim();
        const Result = await this.api('POST', '/api/register-token', { Token, Name: '', Email: '' }, { noAuth: true });
        this._setSession({ token: Token, quotaUsed: Result.used, quotaMax: Result.max, name: Result.name || '' });
        if (Result.name) this._saveUserProfile(Result.name, null);
        this.showRegModal = false;
        this.regForm = { token: '', name: '', email: '' };
        this._enterDesignAfterSignIn(false);   // land on the Design screen, as P2 does
        this.refreshBag();
      } catch (E) {
        this.regError = E.message;
      } finally {
        this.regLoading = false;
      }
    },
    _setSession(S) {
      this.userSession = S;
      this.quotaUsed = S.quotaUsed || 0;
      this.quotaMax = S.quotaMax || 10;
      try { localStorage.setItem(SESSION_KEY, JSON.stringify(S)); } catch (_) {}
    },
    // Log in from a #token=… fragment (the "Continue designing" link). A fragment is never sent to
    // the server, so the token stays out of access logs. Returns true when a token was present.
    _loginFromUrlToken() {
      let UrlToken = '';
      try {
        const Params = new URLSearchParams((location.hash || '').replace(/^#/, ''));
        UrlToken = (Params.get('token') || '').trim();
        if (!UrlToken) return false;
        Params.delete('token');
        const Frag = Params.toString();
        history.replaceState(history.state, '', location.pathname + location.search + (Frag ? '#' + Frag : ''));
      } catch (_) { return false; }
      if (!UrlToken.startsWith('p3_')) UrlToken = UrlToken.toUpperCase();
      this._setSession({ token: UrlToken, quotaUsed: 0, quotaMax: 10 });
      return true;
    },
    // Pull the authoritative quota (P2 _refreshQuota): on load, when the Design screen opens,
    // after generations, and on a quota error.
    async _refreshQuota() {
      if (!this.userSession) return;
      let Q = null;
      try { Q = await this.api('GET', '/api/token-status'); } catch (_) { Q = null; }
      if (!Q || !this.userSession) return;
      this.quotaUsed = Q.used; this.quotaMax = Q.max;
      Object.assign(this.userSession, { quotaUsed: Q.used, quotaMax: Q.max });
      if (Q.name) this.userSession.name = Q.name;
      if (Q.email) this.userSession.email = Q.email;
      try { localStorage.setItem(SESSION_KEY, JSON.stringify(this.userSession)); } catch (_) {}
      if ((Q.name && !this.userProfile.name) || (Q.email && !this.userProfile.email)) {
        this._saveUserProfile(Q.name || null, Q.email || null);
      }
    },
    _saveUserProfile(name, email) {
      this.userProfile = { name: name || this.userProfile.name || '', email: email || this.userProfile.email || '' };
      try { localStorage.setItem(PROFILE_KEY, JSON.stringify(this.userProfile)); } catch (_) {}
    },
    // Where a freshly signed-in user lands: the Design screen, with a clear confirmation.
    _enterDesignAfterSignIn(fromVerification = false) {
      this.showRegModal = false;
      this.closePreview();
      this._doResetAIFlow();
      this.signInNotice = {
        title: fromVerification ? 'Your email has been verified successfully.' : "You're signed in.",
        text: 'You can now continue designing your jewelry.',
      };
      this.loadDesigns();
    },
    dismissSignInNotice() {
      this.signInNotice = null;
      this.$nextTick(() => { const t = document.getElementById('p3-composer-input'); if (t) t.focus(); });
    },
    logout() {
      try { localStorage.removeItem(SESSION_KEY); } catch (_) {}
      this.stopPolling();
      this.userSession = null; this.quotaUsed = 0; this.quotaMax = 10;
      this.signInNotice = null;
      this.design = null; this.cust = null; this.bag = null; this.designs = []; this.designsError = '';
      this.persist({ designId: null, view: 'home' });
      this.navigateTo('home');
    },

    // ── Account panel (opens from the token coin / balance line) ───────
    toggleAccountPanel() {
      this.accountPanelOpen = !this.accountPanelOpen;
      this.tokenCopied = false;
      if (this.accountPanelOpen) this._refreshQuota();
    },
    closeAccountPanel() { this.accountPanelOpen = false; this.tokenCopied = false; },
    async copyAccessToken() {
      const t = this.token;
      if (!t) return;
      try { await navigator.clipboard.writeText(t); }
      catch (_) {
        const ta = document.createElement('textarea');
        ta.value = t; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); } catch (__) {}
        document.body.removeChild(ta);
      }
      this.tokenCopied = true;
      setTimeout(() => { this.tokenCopied = false; }, 2000);
    },
    openMyDesignsFromAccount() {
      this.closeAccountPanel();
      if (this.view !== 'ai-studio') this._doResetAIFlow();
      this.sidebarOpen = true;
      this.loadDesigns();
    },
    signOutFromAccount() { this.closeAccountPanel(); this.logout(); },

    // ── catalog helpers ───────────────────────────────────────────────
    group(id) { return this.catalog?.groups.find(g => g.id === id); },
    materialsOf(groupId) { return this.group(groupId)?.materials || []; },
    material(id) {
      for (const g of this.catalog?.groups || []) for (const m of g.materials) if (m.id === id) return m;
      return null;
    },
    groupOfMaterial(id) { return this.material(id)?.group; },
    copy(id) { return MATERIAL_COPY[id] || {}; },
    get allMaterials() { return (this.catalog?.groups || []).flatMap(g => g.materials.map(m => ({ ...m, groupLabel: g.label }))); },
    get metalsFaqAnswer() {
      const fashion = this.materialsOf('fashion').map(m => m.label).join(', ');
      const gold = this.materialsOf('luxury').map(m => m.label.replace(' Gold', '')).join(', ');
      return `Fashion jewelry: ${fashion}. Luxury: solid gold in ${gold} — luxury pieces can be previewed but are not yet available to order.`;
    },

    // ── compose / new design ──────────────────────────────────────────
    requestUpload() { this.$refs.uploader?.click(); },
    handleUpload(ev) {
      const f = ev.target.files[0];
      ev.target.value = '';
      if (!f) return;
      this.uploadedFile = f;
      this.uploadedPreview = URL.createObjectURL(f);
      this.rightsConfirmed = false;
    },
    clearUpload() { this.uploadedFile = null; this.uploadedPreview = null; this.rightsConfirmed = false; },
    handleEnterKey(ev) { if (!ev.shiftKey) { ev.preventDefault(); this.sendComposer(); } },

    get composerMode() { return this.design ? 'refine' : 'create'; },
    get composerPlaceholder() {
      if (!this.design) return 'Describe your ring…';
      return this.canActOnSelection ? 'Describe how to refine the selected design…' : 'Select one of the designs above to refine it…';
    },
    get canSend() {
      if (this.submitting) return false;
      if (this.composerMode === 'create') return this.userInput.trim().length > 0 || !!this.uploadedFile;
      return this.canActOnSelection && !this.pendingBatch && this.userInput.trim().length > 0;
    },

    async sendComposer() {
      if (this.composerMode === 'refine') return this.refine();
      return this.generate();
    },

    async generate() {
      this.composeError = '';
      const text = this.userInput.trim() || (this.uploadedFile ? 'Process this image' : '');
      if (text.length < 3) { this.composeError = 'Please describe your ring in a few words.'; return; }
      if (this.uploadedFile && !this.rightsConfirmed) { this.composeError = 'Please confirm you have the rights to use the uploaded image.'; return; }
      const fd = new FormData();
      fd.append('prompt', text);
      fd.append('client_request_id', newRequestId());
      if (this.uploadedFile) { fd.append('reference', this.uploadedFile); fd.append('rights_confirmed', 'true'); }
      this.submitting = true;
      try {
        const batch = await this.api('POST', '/api/designs', fd);
        this.userInput = ''; this.clearUpload();
        await this.openDesign(batch.design_id);
        this.loadDesigns();
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
      this.actionError = ''; this.composeError = ''; this.closePreview();
      this.userInput = ''; this.clearUpload();
      this.persist({ designId: null, view: 'ai-studio' });
      if (this.view !== 'ai-studio' && STUDIO_VIEWS.includes(this.view)) this.view = 'ai-studio';
    },

    // ── design state ──────────────────────────────────────────────────
    async openDesign(designId, { restoreView } = {}) {
      this.stopPolling();
      const d = await this.api('GET', `/api/designs/${designId}`);
      this.design = d; this.cust = null; this.pendingRefineBatchId = null;
      this.actionError = ''; this.composeError = '';
      const last = d.batches[d.batches.length - 1];
      // Show the newest batch that has something to show; a still-running refinement is shown as progress.
      const shown = [...d.batches].reverse().find(b => !this.anyActive(b)) || last;
      this.viewBatchId = shown.id;
      if (last.id !== shown.id) this.pendingRefineBatchId = last.id;
      this.persist({ designId });
      if (restoreView === 'review' && d.customization) {
        this.showCustomization(d.customization);
      } else if (restoreView === 'checkout') {
        this.navigateTo('checkout');
      } else {
        this.navigateTo('ai-studio');
      }
      if (window.innerWidth < 1024) this.sidebarOpen = false;
      this.ensurePolling();
    },

    batch(id) { return this.design?.batches.find(b => b.id === id); },
    get viewBatch() { return this.batch(this.viewBatchId); },
    get pendingBatch() { return this.batch(this.pendingRefineBatchId); },
    get visibleBatches() { return (this.design?.batches || []).filter(b => b.id !== this.pendingRefineBatchId); },
    get selectedId() { return this.design?.selected_candidate_id || null; },
    get selectedCandidate() {
      for (const b of this.design?.batches || []) for (const c of b.candidates) if (c.id === this.selectedId) return c;
      return null;
    },
    get canActOnSelection() { return this.selectedCandidate?.status === 'ready'; },
    readyCount(b) { return b ? b.candidates.filter(c => c.status === 'ready').length : 0; },
    batchLabel(b) {
      if (b.kind === 'initial') return 'Original';
      const n = this.design.batches.filter(x => x.kind === 'refine').indexOf(b) + 1;
      return `Refinement ${n}`;
    },
    anyActive(b) { return !!b && (b.status === 'queued' || b.status === 'generating'); },
    // The P2 waiting movie is shown while a new design or a refinement is being generated.
    get waitBatch() {
      if (this.pendingBatch) return this.pendingBatch;
      return this.anyActive(this.viewBatch) ? this.viewBatch : null;
    },
    get waitVariant() { return this.waitBatch ? (this.waitBatch.kind === 'refine' ? 'refine' : 'design') : null; },
    get waitPreset() { return WAIT_PRESETS[this.waitVariant || 'design']; },
    get waitStatus() { const s = this.waitPreset.statuses; return s[this.waitStatusIndex % s.length]; },
    get bubbleBatch() { return this.pendingBatch || this.viewBatch; },
    get viewBatchGenerating() { return this.anyActive(this.viewBatch) && this.readyCount(this.viewBatch) === 0; },

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
      if (!this.anyActive(b)) this._refreshQuota();
      // Refresh My Designs thumbnails once a batch has images to show.
      if (this.sidebarOpen && !this.anyActive(b)) this.loadDesigns();
      if (b.id === this.pendingRefineBatchId && !this.anyActive(b)) {
        this.pendingRefineBatchId = null;
        if (b.status === 'failed') this.actionError = 'The refinement could not be generated. You can retry the failed designs.';
        // Switch to the new batch so the user can choose again; the earlier batch stays in history.
        this.viewBatchId = b.id;
        this.setSelection(null);
      }
    },

    async select(c) {
      if (!c || c.status !== 'ready') return;
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

    async retrySlot(c) {
      this.actionError = '';
      try {
        const b = await this.api('POST', `/api/candidates/${c.id}/retry`);
        this.replaceBatch(b);
        this.ensurePolling();
      } catch (e) { this.actionError = e.message; }
    },
    async retryBatch(b) {
      this.actionError = '';
      try {
        this.replaceBatch(await this.api('POST', `/api/batches/${b.id}/retry-failed`));
        this.ensurePolling();
      } catch (e) { this.actionError = e.message; }
    },
    replaceBatch(b) {
      const idx = this.design?.batches.findIndex(x => x.id === b.id);
      if (idx >= 0) this.design.batches.splice(idx, 1, b);
    },

    // ── refine ────────────────────────────────────────────────────────
    focusRefine() {
      this.actionError = '';
      if (!this.canActOnSelection) { this.actionError = 'Select one of the designs to refine it.'; return; }
      if (this.userInput.trim().length >= 3) { this.refine(); return; }
      this.$refs.composer?.focus();
    },
    async refine() {
      this.composeError = '';
      if (!this.canActOnSelection) { this.composeError = 'Select one of the designs to refine it.'; return; }
      const text = this.userInput.trim();
      if (text.length < 3) { this.composeError = 'Describe the change you want.'; return; }
      const designId = this.design.id;
      this.submitting = true;
      try {
        const b = await this.api('POST', `/api/designs/${designId}/batches`, {
          parent_candidate_id: this.selectedId, instruction: text, client_request_id: newRequestId(),
        });
        if (this.design?.id !== designId) return;
        this.design.batches.push(b);
        this.pendingRefineBatchId = b.id;      // keep the current grid until the new batch is ready
        this.userInput = '';
        this.ensurePolling();
      } catch (e) {
        this.composeError = e.message;         // e.g. reference_unavailable — no text-only fallback
      } finally {
        this.submitting = false;
      }
    },

    // ── my designs sidebar ────────────────────────────────────────────
    async loadDesigns() {
      if (!this.token) return;
      this.designsLoading = true; this.designsError = '';
      try { this.designs = (await this.api('GET', '/api/designs')).designs; }
      catch (e) { this.designsError = e.message; }
      finally { this.designsLoading = false; }
    },
    get filteredDesigns() {
      const q = this.projectSearch.trim().toLowerCase();
      return q ? this.designs.filter(d => d.title.toLowerCase().includes(q)) : this.designs;
    },
    toggleSidebar() { this.sidebarOpen = !this.sidebarOpen; if (this.sidebarOpen) this.loadDesigns(); },

    // ── preview overlay ───────────────────────────────────────────────
    openPreview(media, src, candidate = null) {
      this.previewMedia = media; this.previewSrc = src; this.previewCandidate = candidate;
      this.previewResetZoom(); this.previewOpen = true;
    },
    closePreview() { this.previewOpen = false; this.previewCandidate = null; },
    previewZoomBy(f) {
      this.previewZoom = Math.min(6, Math.max(1, this.previewZoom * f));
      if (this.previewZoom === 1) { this.previewPanX = 0; this.previewPanY = 0; }
    },
    previewResetZoom() { this.previewZoom = 1; this.previewPanX = 0; this.previewPanY = 0; },
    previewPanDown(ev) {
      if (this.previewZoom <= 1) { this.previewZoomBy(2); return; }
      this._panning = true; this._panStart = { x: ev.clientX - this.previewPanX, y: ev.clientY - this.previewPanY };
      ev.target.setPointerCapture?.(ev.pointerId);
    },
    previewPanMove(ev) {
      if (!this._panning) return;
      this.previewPanX = ev.clientX - this._panStart.x; this.previewPanY = ev.clientY - this._panStart.y;
    },
    previewPanEnd() { this._panning = false; },
    async selectFromPreview() { if (this.previewCandidate) await this.select(this.previewCandidate); },

    // ── customize ─────────────────────────────────────────────────────
    async proceed() {
      if (!this.canActOnSelection) { this.actionError = 'Select one of the designs to customize it.'; return; }
      this.actionError = '';
      const designId = this.design.id, candidateId = this.selectedId;
      // Show the selected image immediately; the server returns the authoritative customization.
      this.cust = { candidate_id: candidateId, image_url: this.selectedCandidate.image_url, material_id: 'silver',
                    quote: null, movie: { status: 'queued' }, ring_size: null, quantity: 1 };
      this.mediaTab = 'movie'; this.custError = ''; this.bagMessage = '';
      this.groupOpen = { fashion: true, luxury: false };
      this.navigateTo('review');
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
      this.mediaTab = 'movie';          // the movie is the default Customize view
      const g = this.groupOfMaterial(c.material_id) || 'fashion';
      this.lastMaterialByGroup[g] = c.material_id;
      this.groupOpen = { fashion: g === 'fashion', luxury: g === 'luxury' };
      if (this.view !== 'review') this.navigateTo('review');
      this.pollCustomization();
    },

    pollCustomization() {
      if (this._pollCust) clearInterval(this._pollCust);
      const custId = this.cust?.id;
      const tick = async () => {
        if (!this.cust || this.cust.id !== custId || this.view !== 'review') { clearInterval(this._pollCust); this._pollCust = null; return; }
        const st = this.cust.movie?.status;
        if (st !== 'queued' && st !== 'running') { clearInterval(this._pollCust); this._pollCust = null; return; }
        try {
          const fresh = await this.api('GET', `/api/customizations/${custId}`);
          if (this.cust?.id === custId) {
            if (fresh.movie?.status === 'ready' && this.cust.movie?.status !== 'ready') this._refreshQuota();
            this.cust.movie = fresh.movie;
          }
        } catch { /* retry next tick */ }
      };
      this._pollCust = setInterval(tick, 2000);
    },

    async retryMovie() {
      if (!this.cust) return;
      const custId = this.cust.id;
      this.custError = '';
      try {
        const m = await this.api('POST', `/api/candidates/${this.cust.candidate_id}/movie`);
        if (this.cust?.id === custId) { this.cust.movie = m; this.mediaTab = 'movie'; this.pollCustomization(); }
      } catch (e) { this.custError = e.message; }
    },

    get movieStatus() { return this.cust?.movie?.status || null; },
    get movieBusy() { return this.movieStatus === 'queued' || this.movieStatus === 'running'; },
    get movieFailed() { return this.movieStatus === 'failed' || this.movieStatus === 'interrupted'; },
    get currentGroup() { return this.groupOfMaterial(this.cust?.material_id) || 'fashion'; },
    get currentMaterial() { return this.material(this.cust?.material_id); },
    get tint() { return this.currentMaterial?.tint || ''; },

    toggleGroup(groupId) {
      // Selecting a group reveals its options and selects that group's last-used option;
      // clicking the active group again just collapses/expands it.
      if (this.currentGroup === groupId) { this.groupOpen[groupId] = !this.groupOpen[groupId]; return; }
      this.groupOpen = { fashion: groupId === 'fashion', luxury: groupId === 'luxury' };
      const target = this.lastMaterialByGroup[groupId] || this.materialsOf(groupId)[0]?.id;
      if (target) this.chooseMaterial(target);
    },

    async chooseMaterial(materialId) {
      if (!this.cust?.id || this.cust.material_id === materialId) return;
      const custId = this.cust.id;
      const g = this.groupOfMaterial(materialId);
      this.lastMaterialByGroup[g] = materialId;
      this.groupOpen = { fashion: g === 'fashion', luxury: g === 'luxury' };
      // Never leave a previous fashion price visible while the new quote loads.
      this.cust.material_id = materialId;
      this.cust.quote = null; this.cust.line_total = null; this.cust.can_add_to_bag = false;
      this.quotePending = true; this.bagMessage = '';
      await this.patchCustomization(custId, { material_id: materialId });
    },

    async setSize(v) {
      if (!this.cust?.id) return;
      await this.patchCustomization(this.cust.id, { ring_size: v === '' || v === null ? null : Number(v) });
    },
    async setQuantity(delta) {
      if (!this.cust?.id) return;
      const q = Math.min(10, Math.max(1, (this.cust.quantity || 1) + delta));
      if (q !== this.cust.quantity) await this.patchCustomization(this.cust.id, { quantity: q });
    },

    async patchCustomization(custId, patch) {
      this.custError = '';
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
    get priceAvailable() { return !this.isLuxury && this.cust?.quote?.pricing_status === 'available'; },
    get priceText() {
      if (this.isLuxury) return 'Price unavailable';
      const q = this.cust?.quote;
      if (!q) return '—';
      if (q.pricing_status !== 'available') return 'Price unavailable';
      return this.money(q.unit_price, q.currency);
    },
    money(v, cur = 'USD') {
      return new Intl.NumberFormat('en-US', { style: 'currency', currency: cur }).format(v);
    },
    get bagButtonText() {
      if (!this.cust || this.quotePending) return 'Updating price…';
      switch (this.cust.add_to_bag_blocked_reason) {
        case 'luxury_preview_only': return 'Preview Only';
        case 'price_unavailable': return 'Price Unavailable';
        case 'ring_size_required': return 'Select a Ring Size';
        default: return 'Add to Bag';
      }
    },
    get bagBlockedText() {
      if (!this.cust) return '';
      switch (this.cust.add_to_bag_blocked_reason) {
        case 'luxury_preview_only': return 'Luxury pieces can be previewed but are not yet available to order.';
        case 'price_unavailable': return 'Price unavailable — this piece cannot be added to the bag yet.';
        case 'ring_size_required': return 'Choose your ring size to continue.';
        default: return '';
      }
    },
    sizeGuideRows(kind) {
      const mm = { 4: '14.9', 4.5: '15.3', 5: '15.7', 5.5: '16.1', 6: '16.5', 6.5: '16.9', 7: '17.3', 7.5: '17.7', 8: '18.1',
                   8.5: '18.5', 9: '19.0', 9.5: '19.4', 10: '19.8', 10.5: '20.2', 11: '20.6', 11.5: '21.0', 12: '21.4' };
      const circ = { 4: '46.8', 4.5: '48.1', 5: '49.3', 5.5: '50.6', 6: '51.9', 6.5: '53.1', 7: '54.4', 7.5: '55.6', 8: '57.0',
                     8.5: '58.1', 9: '59.5', 9.5: '60.9', 10: '62.1', 10.5: '63.5', 11: '64.6', 11.5: '66.0', 12: '67.2' };
      const uk = { 4: 'H 1/2', 4.5: 'I 1/2', 5: 'J 1/2', 5.5: 'K 1/2', 6: 'L 1/2', 6.5: 'M 1/2', 7: 'O', 7.5: 'P', 8: 'Q',
                   8.5: 'R', 9: 'S', 9.5: 'T', 10: 'U', 10.5: 'V', 11: 'W', 11.5: 'X', 12: 'Y' };
      const de = { 4: '15', 4.5: '15.25', 5: '15.75', 5.5: '16', 6: '16.5', 6.5: '16.75', 7: '17.25', 7.5: '17.75', 8: '18',
                   8.5: '18.5', 9: '19', 9.5: '19.5', 10: '19.75', 10.5: '20.25', 11: '20.5', 11.5: '21', 12: '21.5' };
      return (this.catalog?.ring_sizes.values || []).map(us => ({ us, m: (kind === 'ring' ? mm : circ)[us], uk: uk[us], de: de[us] }));
    },
    pickGuideSize(us) { this.setSize(us); this.showSizeGuide = false; },

    async addToBag() {
      if (!this.cust?.can_add_to_bag) return;
      this.bagMessage = ''; this.custError = '';
      try {
        this.bag = await this.api('POST', '/api/bag', { customization_id: this.cust.id });
        this.bagMessage = 'Added to your bag.';
      } catch (e) { this.custError = e.message; }
    },

    backToDesign() { this.navigateTo('ai-studio'); },

    // ── bag ───────────────────────────────────────────────────────────
    get bagLines() { return this.bag?.lines || []; },
    get bagCount() { return this.bagLines.reduce((n, l) => n + l.quantity, 0); },
    async refreshBag() { if (!this.token) return; try { this.bag = await this.api('GET', '/api/bag'); } catch { /* ignore */ } },
    async removeLine(l) { try { this.bag = await this.api('DELETE', `/api/bag/${l.id}`); } catch (e) { this.custError = e.message; } },
    goToCheckout() { this.navigateTo('checkout'); },
  };
}

window.p3App = p3App;

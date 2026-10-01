// P3 Admin — Dashboard | Sessions | Users. Talks only to {base}/api/admin/* with the admin key as a Bearer
// token (today the developer key; later an admin role / company sign-in replaces RequireAdmin).
const BASE = document.querySelector('meta[name="p3-base"]')?.content || '';
const KEY_STORE = 'p3_admin_key' + (BASE ? ':' + BASE : '');   // sessionStorage: this tab only

const STATUS = {
  active: ['Active', 'bg-emerald-100 text-emerald-800'],
  unused: ['Unused', 'bg-amber-100 text-amber-800'],
  pending_verification: ['Pending verification', 'bg-sky-100 text-sky-800'],
  exhausted: ['Exhausted', 'bg-red-100 text-red-700'],
  inactive: ['Inactive', 'bg-zinc-200 text-zinc-600'],
  removed: ['Removed', 'bg-zinc-800 text-white'],
};
const STEPS = {
  started: ['Started', 'bg-zinc-900 text-white'],
  generate_requested: ['Prompt', 'bg-zinc-100 text-zinc-600'],
  generated: ['Generated', 'bg-blue-100 text-blue-700'],
  refine_requested: ['Refine request', 'bg-zinc-100 text-zinc-600'],
  refined: ['Refined', 'bg-violet-100 text-violet-700'],
  option_selected: ['Option selected', 'bg-zinc-100 text-zinc-600'],
  customize_opened: ['Customize', 'bg-amber-100 text-amber-800'],
  customization_changed: ['Changed choice', 'bg-amber-50 text-amber-800'],
  movie: ['360° movie', 'bg-purple-100 text-purple-700'],
  bag_added: ['Added to Bag', 'bg-emerald-100 text-emerald-800'],
  bag_removed: ['Removed from Bag', 'bg-zinc-200 text-zinc-600'],
  bag_viewed: ['Viewed Bag', 'bg-zinc-100 text-zinc-600'],
  checkout_clicked: ['Checkout Clicked', 'bg-emerald-600 text-white'],
  design_opened: ['Reopened', 'bg-zinc-100 text-zinc-600'],
  admin_3d_requested: ['Admin: 3D requested', 'bg-green-100 text-green-700'],
  admin_3d_measured: ['Admin: 3D measured', 'bg-green-100 text-green-700'],
};
const THREE_D = {
  requested: ['Requested', 'bg-zinc-100 text-zinc-600'], generating: ['Hi3D running', 'bg-sky-100 text-sky-800'],
  measuring: ['Measuring', 'bg-sky-100 text-sky-800'], measured: ['Measured', 'bg-emerald-100 text-emerald-800'],
  needs_review: ['Needs review', 'bg-amber-100 text-amber-800'], failed: ['Failed', 'bg-red-100 text-red-700'],
};
const EVENTS = {
  design_created: ['Design', 'bg-blue-100 text-blue-700'],
  refinement: ['Refinement', 'bg-violet-100 text-violet-700'],
  movie: ['360° movie', 'bg-purple-100 text-purple-700'],
  mesh: ['3D', 'bg-green-100 text-green-700'],
  bag_add: ['Added to bag', 'bg-zinc-900 text-white'],
  sign_in: ['Sign-in', 'bg-zinc-100 text-zinc-600'],
  admin_created: ['Created by admin', 'bg-zinc-100 text-zinc-600'],
  admin_edited: ['Edited by admin', 'bg-zinc-100 text-zinc-600'],
  activated: ['Activated', 'bg-emerald-100 text-emerald-800'],
  deactivated: ['Deactivated', 'bg-zinc-200 text-zinc-600'],
  removed: ['Removed', 'bg-zinc-800 text-white'],
  restored: ['Restored', 'bg-emerald-100 text-emerald-800'],
};

function adminApp() {
  return {
    BASE,
    key: '', keyInput: '', ok: false, busy: false, error: '', mode: '',
    users: [], search: '', showRemoved: false,
    form: { Name: '', Email: '', MaxGenerations: 10 },
    created: null, createError: '', duplicateOf: null, copied: null,
    editing: null, edit: {}, editError: '',
    userId: '', d: null, detailError: '', openDesign: null,
    tab: 'sessions', materials: {},
    dash: null, sessions: [], idleMinutes: 30, sq: '', sStage: '', sBag: '', s3d: '',
    sessionId: '', sd: null, sdError: '', g3: { size: 10, material: '', busy: false, error: '' },
    stageOptions: [['started', 'Started'], ['generated', 'Generated'], ['customize', 'Customize'], ['bag', 'Bag'], ['checkout_clicked', 'Checkout Clicked']],
    chartKinds: [
      { key: 'images', label: 'Images', color: '#3b82f6' },
      { key: 'refinement_images', label: 'Refinement images', color: '#8b5cf6' },
      { key: 'movies', label: '360° movies', color: '#9A7230' },
      { key: 'meshes', label: '3D', color: '#16a34a' },
    ],

    async init() {
      try { this.key = sessionStorage.getItem(KEY_STORE) || ''; } catch (_) {}
      window.addEventListener('hashchange', () => this.route());
      if (this.key) await this.connect(this.key);
    },

    async api(method, path, body) {
      const r = await fetch(BASE + path, {
        method, headers: { Authorization: 'Bearer ' + this.key, ...(body ? { 'Content-Type': 'application/json' } : {}) },
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await r.json().catch(() => ({}));
      if (r.status === 401 || r.status === 403) { this.ok = false; this.error = 'The admin key was not accepted.'; }
      if (!r.ok) { const e = new Error(data?.error?.message || ('Request failed (' + r.status + ')')); e.code = data?.error?.code; throw e; }
      return data;
    },

    async connect(given) {
      this.key = (given || this.keyInput).trim();
      this.busy = true; this.error = '';
      try {
        const s = await this.api('GET', '/api/admin/session');
        this.mode = s.mode; this.ok = true; this.keyInput = '';
        try { sessionStorage.setItem(KEY_STORE, this.key); } catch (_) {}
        try {
          const cat = await (await fetch(BASE + '/api/catalog')).json();
          for (const g of cat.groups || []) for (const m of g.materials || []) this.materials[m.id] = m.label;
        } catch (_) {}
        this.route();
      } catch (e) {
        this.ok = false; this.error = e.message;
        try { sessionStorage.removeItem(KEY_STORE); } catch (_) {}
      } finally { this.busy = false; }
    },
    signOut() {
      try { sessionStorage.removeItem(KEY_STORE); } catch (_) {}
      this.key = ''; this.ok = false; this.users = []; this.d = null;
    },

    // ── routing: #/dashboard  #/sessions[/<id>]  #/users[/<account id>] ──
    go(hash) { if (location.hash === hash) this.route(); else location.hash = hash; },
    async route() {
      const m = location.hash.match(/^#\/(dashboard|sessions|users)(?:\/(.+))?$/);
      this.tab = m ? m[1] : 'sessions';
      const id = m && m[2] ? decodeURIComponent(m[2]) : '';
      this.userId = this.tab === 'users' ? id : '';
      this.sessionId = this.tab === 'sessions' ? id : '';
      this.openDesign = null;
      window.scrollTo({ top: 0 });
      if (!this.ok) return;
      if (this.tab === 'dashboard') this.dash = await this.api('GET', '/api/admin/dashboard').catch(() => null);
      if (this.tab === 'sessions' && !id) await this.loadSessions();
      if (this.tab === 'sessions' && id) await this.loadSession();
      if (this.tab === 'users' && !id) await this.load();
      if (this.tab === 'users' && id) await this.loadDetail(); else this.d = null;
    },

    // ── list ───────────────────────────────────────────────────────────
    async load() {
      const r = await this.api('GET', '/api/admin/users' + (this.showRemoved ? '?include_removed=true' : ''));
      this.users = r.users;
    },
    get filtered() {
      const q = this.search.trim().toLowerCase();
      return q ? this.users.filter(u => [u.name, u.email, u.token].some(v => (v || '').toLowerCase().includes(q))) : this.users;
    },
    async create() {
      this.busy = true; this.createError = ''; this.duplicateOf = null; this.created = null;
      try {
        const u = await this.api('POST', '/api/admin/users', this.form);
        this.created = u;
        this.form = { Name: '', Email: '', MaxGenerations: 10 };
        await this.load();
      } catch (e) {
        this.createError = e.message;
        if (e.code === 'duplicate_email') this.duplicateOf = (e.message.match(/\((p3local:[^)]+)\)/) || [])[1] || null;
      } finally { this.busy = false; }
    },
    startEdit(u) {
      this.editing = u.account_id; this.editError = '';
      this.edit = { Name: u.name, Email: u.email, MaxGenerations: u.max, ResetUsage: false };
    },
    async saveEdit(u) {
      this.busy = true; this.editError = '';
      try {
        await this.api('PATCH', '/api/admin/users/' + encodeURIComponent(u.account_id), this.edit);
        this.editing = null; await this.load();
      } catch (e) { this.editError = e.message; } finally { this.busy = false; }
    },
    async act(u, action) {
      try {
        await this.api('POST', '/api/admin/users/' + encodeURIComponent(u.account_id) + '/' + action);
        await this.load();
        if (this.userId) await this.loadDetail();
      } catch (e) { alert(e.message); }
    },
    async remove(u) {
      if (!confirm(`Remove ${u.name || u.email || u.token}?\n\nTheir token stops working immediately. Designs and usage history are kept, and the user can be restored from "Show removed".`)) return;
      await this.act(u, 'remove');
    },
    async copy(text) {
      if (!text) return;
      try { await navigator.clipboard.writeText(text); } catch (_) {}
      this.copied = text; setTimeout(() => { if (this.copied === text) this.copied = null; }, 2000);
    },

    // ── sessions ───────────────────────────────────────────────────────
    async loadSessions() {
      const r = await this.api('GET', '/api/admin/sessions');
      this.sessions = r.sessions; this.idleMinutes = r.idle_minutes;
    },
    get sessionsFiltered() {
      const q = this.sq.trim().toLowerCase();
      return this.sessions.filter(x =>
        (!q || [x.customer_name, x.customer_email, x.title, x.prompt].some(v => (v || '').toLowerCase().includes(q))) &&
        (!this.sStage || x.stage_reached === this.sStage) &&
        (!this.sBag || (this.sBag === 'yes') === x.add_to_bag) &&
        (!this.s3d || (this.s3d === 'any') === !!x.three_d_status));
    },
    async loadSession() {
      this.sdError = ''; this.g3.error = '';
      try {
        const sd = await this.api('GET', '/api/admin/sessions/' + encodeURIComponent(this.sessionId));
        const keep = this.sd?.session.session_id === sd.session.session_id;    // keep the admin's choice on refresh
        const size = keep ? this.g3.size : sd.three_d_defaults.production_size;
        const material = keep ? this.g3.material : sd.three_d_defaults.material_id;
        this.g3.size = null; this.g3.material = '';
        this.sd = sd;
        await this.$nextTick();                 // the <option>s must exist before the selects get their value
        await new Promise(r => setTimeout(r));  // (a freshly created detail block renders its options a tick later)
        this.g3.size = size; this.g3.material = material;
        if (this.sd.three_d.some(t => ['requested', 'generating', 'measuring'].includes(t.status)))
          setTimeout(() => { if (this.sessionId === this.sd?.session.session_id) this.loadSession(); }, 2000);
      } catch (e) { this.sd = null; this.sdError = e.message; }
    },
    async generate3d() {
      const custom = this.sd.three_d_defaults.customer_size ?? 10;
      const paid = !this.sd.three_d_defaults.has_raw_mesh;
      const msg = (paid ? `Generate 3D with Hi3D v3.0 for this session?\n\nThis is a ${this.mode === 'mock' ? 'MOCK (free, simulated)' : 'PAID live'} Hi3D call.` : 'Recalculate the existing Hi3D model?')
        + `\n\nSize: US ${this.g3.size}${this.g3.size !== custom ? ' (manual override)' : ''}\nMaterial: ${this.materialLabel(this.g3.material)}`;
      if (!confirm(msg)) return;
      this.g3.busy = true; this.g3.error = '';
      try {
        await this.api('POST', '/api/admin/sessions/' + encodeURIComponent(this.sessionId) + '/3d',
          { production_size: this.g3.size, material_id: this.g3.material });
        await this.loadSession();
      } catch (e) { this.g3.error = e.message; } finally { this.g3.busy = false; }
    },
    async downloadStl(id, stage) {
      const r = await fetch(BASE + `/api/admin/3d/${encodeURIComponent(id)}/stl/${stage}`, { headers: { Authorization: 'Bearer ' + this.key } });
      if (!r.ok) { alert('Download failed (' + r.status + ')'); return; }
      const url = URL.createObjectURL(await r.blob());
      const a = Object.assign(document.createElement('a'), { href: url, download: `${id}_${stage}.stl` });
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    },

    // ── detail ─────────────────────────────────────────────────────────
    async loadDetail() {
      this.detailError = '';
      try { this.d = await this.api('GET', '/api/admin/users/' + encodeURIComponent(this.userId)); }
      catch (e) { this.d = null; this.detailError = e.message; }
    },
    statCards() {
      const t = this.d.totals;
      const jobs = (j) => `${j.by_status.ready || 0} ready · ${(j.by_status.failed || 0) + (j.by_status.interrupted || 0)} failed`;
      const prov = (j) => Object.entries(j.by_provider).map(([k, v]) => `${v} ${k === 'fal' ? 'live' : k.replace('_', ' ')}`).join(' · ');
      return [
        { label: 'Image generations', value: t.images.total, sub: jobs(t.images) },
        { label: 'Refinements', value: t.refinements, sub: `${t.refinement_images.total} images · ${jobs(t.refinement_images)}` },
        { label: '360° movies', value: t.movies.total, sub: jobs(t.movies) },
        { label: '3D generations', value: t.meshes.total, sub: t.meshes.total ? jobs(t.meshes) : 'Developer tool only' },
        { label: 'Designs created', value: t.designs, sub: 'Every design is saved automatically' },
        { label: 'Added to bag', value: t.bag_lines, sub: `${t.bag_units} ring${t.bag_units === 1 ? '' : 's'}` },
        { label: 'AI requests by provider', value: t.images.total + t.refinement_images.total + t.movies.total + t.meshes.total,
          sub: prov({ by_provider: this.sumProviders(t) }) || '—' },
      ];
    },
    sumProviders(t) {
      const out = {};
      for (const j of [t.images, t.refinement_images, t.movies, t.meshes])
        for (const [k, v] of Object.entries(j.by_provider)) out[k] = (out[k] || 0) + v;
      return out;
    },
    barClass() {
      const r = this.d.totals.generations_used / Math.max(1, this.d.totals.generations_max);
      return r >= 1 ? 'bg-red-500' : r >= 0.75 ? 'bg-amber-500' : 'bg-emerald-500';
    },
    chartDays() {
      const byDay = Object.fromEntries((this.d?.daily || []).map(x => [x.day, x]));
      const out = [];
      const today = new Date();
      for (let i = 89; i >= 0; i--) {
        const dt = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
        const day = dt.toISOString().slice(0, 10);
        out.push(byDay[day] || { day });
      }
      return out;
    },
    chartMax() {
      return Math.max(1, ...this.chartDays().map(x => this.chartKinds.reduce((s, k) => s + (x[k.key] || 0), 0)));
    },
    dayTitle(x) {
      const parts = this.chartKinds.filter(k => x[k.key]).map(k => `${x[k.key]} ${k.label.toLowerCase()}`);
      if (x.designs) parts.push(`${x.designs} design${x.designs === 1 ? '' : 's'}`);
      if (x.sign_ins) parts.push(`${x.sign_ins} sign-in${x.sign_ins === 1 ? '' : 's'}`);
      return x.day + (parts.length ? ': ' + parts.join(', ') : ': no activity');
    },

    // ── formatting ─────────────────────────────────────────────────────
    materialLabel(id) { return this.materials[id] || id || '—'; },
    priceText(p) { return !p ? '—' : p.unit_price == null ? 'Unavailable' : '$' + Number(p.unit_price).toFixed(2); },
    fixedSource(p) { return !p ? '' : { bag_snapshot: 'price at Add to Bag', shown_at_customize: 'price shown in Customize', current_quote: 'current list price' }[p.source] || ''; },
    threeDLabel(s) { return (THREE_D[s] || [s])[0]; },
    threeDClass(s) { return (THREE_D[s] || [, 'bg-zinc-100'])[1]; },
    sizeSource(s) { return { customer: 'customer', default: 'default', admin_override: 'manual override' }[s] || s; },
    mm(v) { return v == null ? '—' : Number(v).toFixed(2); },
    innerDia(size) { return (11.63 + 0.8128 * size).toFixed(2); },
    since(a, b) {
      const s = Math.max(0, (new Date(b) - new Date(a)) / 1000);
      return s < 60 ? Math.round(s) + 's' : s < 3600 ? Math.round(s / 60) + 'm' : s < 86400 ? (s / 3600).toFixed(1) + 'h' : (s / 86400).toFixed(1) + 'd';
    },
    stepLabel(e) { const l = (STEPS[e.kind] || [e.kind])[0]; return e.status && e.status !== 'ready' ? `${l} · ${e.status}` : l; },
    stepClass(e) { return e.status === 'failed' ? 'bg-red-100 text-red-700' : (STEPS[e.kind] || [, 'bg-zinc-100'])[1]; },
    stepText(e) {
      const d = e.data || {};
      if (e.text) return e.text;
      if (e.kind === 'customization_changed' || e.kind === 'customize_opened')
        return [d.material_id && this.materialLabel(d.material_id), d.ring_size != null && 'US ' + d.ring_size,
                d.quantity && d.quantity > 1 && '×' + d.quantity, d.unit_price != null && '$' + Number(d.unit_price).toFixed(2)].filter(Boolean).join(' · ');
      if (e.kind === 'bag_added') return [this.materialLabel(d.material_id), d.ring_size != null && 'US ' + d.ring_size, d.unit_price != null && '$' + Number(d.unit_price).toFixed(2)].filter(Boolean).join(' · ');
      if (e.kind.startsWith('admin_3d')) return [d.production_size && 'US ' + d.production_size, d.material_id && this.materialLabel(d.material_id),
                d.reused_raw_mesh && 'reused Hi3D model', d.status, d.weight_g != null && d.weight_g + ' g'].filter(Boolean).join(' · ');
      return '';
    },
    statusLabel(s) { return (STATUS[s] || [s])[0]; },
    statusClass(s) { return (STATUS[s] || [, 'bg-zinc-100'])[1]; },
    eventLabel(e) { const l = (EVENTS[e.kind] || [e.kind])[0]; return e.status && e.status !== 'ready' ? `${l} · ${e.status}` : l; },
    eventClass(e) { return e.status === 'failed' || e.status === 'interrupted' ? 'bg-red-100 text-red-700' : (EVENTS[e.kind] || [, 'bg-zinc-100'])[1]; },
    kindLabel(k) { return { image: 'Image', movie: '360° movie', mesh: '3D' }[k] || k; },
    date(iso) { return iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—'; },
    dateTime(iso) { return iso ? new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'; },
    ago(iso) {
      if (!iso) return '—';
      const s = (Date.now() - new Date(iso).getTime()) / 1000;
      if (s < 60) return 'just now';
      if (s < 3600) return Math.floor(s / 60) + ' min ago';
      if (s < 86400) return Math.floor(s / 3600) + ' h ago';
      if (s < 86400 * 30) return Math.floor(s / 86400) + ' d ago';
      return this.date(iso);
    },
  };
}

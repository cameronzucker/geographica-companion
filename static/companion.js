/* Geographica Companion — Browser UI */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let csrfToken = '';
let outputDir = '';
let map = null;
let bboxLayer = null;
let bboxDrawMode = false;  // true when "Draw Box" is active
let bboxDrawing = false;   // true during an active drag
let bboxStart = null;
let pollTimer = null;
let piHost = '';

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
    try {
        const resp = await fetch('/api/config');
        const cfg = await resp.json();
        csrfToken = cfg.csrf_token;
        outputDir = cfg.output_dir;
        const outputDirEl = document.getElementById('output-dir');
        if (outputDirEl) outputDirEl.value = outputDir;
        log('Config loaded. GDAL: ' + (cfg.gdal_available ? 'available' : 'NOT FOUND'));
    } catch (e) {
        log('Failed to load config: ' + e.message);
    }
    refreshDisk();
    refreshStatus();
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function switchTab(name) {
    document.querySelectorAll('.tab-content').forEach(el => {
        el.classList.remove('active');
    });
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    const tab = document.getElementById('tab-' + name);
    if (tab) tab.classList.add('active');
    // Find matching button by text
    document.querySelectorAll('.tab-btn').forEach(btn => {
        if (btn.textContent.toLowerCase().replace(/\s/g, '') === name.toLowerCase()) {
            btn.classList.add('active');
        }
    });

    if (name === 'transfer') {
        // Pre-fill host from connect tab
        const connectHost = document.getElementById('pi-host').value;
        const transferHost = document.getElementById('transfer-host');
        if (connectHost && !transferHost.value) {
            transferHost.value = connectHost;
        }
        refreshDisk();
        updateManualCommands();
    }
    if (name === 'status') {
        refreshDisk();
        refreshStatus();
    }
}

// ---------------------------------------------------------------------------
// Connect
// ---------------------------------------------------------------------------

async function connectToPi() {
    const host = document.getElementById('pi-host').value.trim();
    if (!host) {
        showConnectStatus('Enter a hostname or IP address.', 'warn');
        return;
    }
    piHost = host;
    showConnectStatus('Connecting to ' + host + '...', 'info');
    const tileUrl = 'http://' + host + ':8090/styles/positron/{z}/{x}/{y}.png';

    // Test tile availability
    try {
        const img = new Image();
        const loaded = await new Promise((resolve) => {
            img.onload = () => resolve(true);
            img.onerror = () => resolve(false);
            img.src = 'http://' + host + ':8090/styles/positron/0/0/0.png';
            setTimeout(() => resolve(false), 5000);
        });
        if (loaded) {
            showConnectStatus('Connected to Pi tile server.', 'ok');
            initMap(tileUrl);
        } else {
            showConnectStatus('Pi tile server not reachable. Try CDN fallback.', 'warn');
        }
    } catch (e) {
        showConnectStatus('Connection failed: ' + e.message, 'error');
    }
}

function useCdnFallback() {
    showConnectStatus('Using OpenStreetMap CDN tiles.', 'info');
    initMap('https://tile.openstreetmap.org/{z}/{x}/{y}.png');
}

function skipMap() {
    showConnectStatus('Map skipped. Enter bbox coordinates manually.', 'info');
}

function showConnectStatus(msg, type) {
    const el = document.getElementById('connect-status');
    el.className = 'status status-' + type;
    el.textContent = msg;
}

// ---------------------------------------------------------------------------
// Map + Bbox Drawing
// ---------------------------------------------------------------------------

function initMap(tileUrl) {
    if (map) {
        map.remove();
        map = null;
    }
    map = new maplibregl.Map({
        container: 'minimap',
        style: {
            version: 8,
            sources: {
                basemap: {
                    type: 'raster',
                    tiles: [tileUrl],
                    tileSize: 256,
                }
            },
            layers: [{
                id: 'basemap',
                type: 'raster',
                source: 'basemap',
            }]
        },
        center: [-112, 38],
        zoom: 3,
        attributionControl: false,
    });

    map.addControl(new maplibregl.NavigationControl(), 'top-right');

    // Bbox drawing
    map.on('load', () => {
        map.addSource('bbox', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
        map.addLayer({
            id: 'bbox-fill',
            type: 'fill',
            source: 'bbox',
            paint: {
                'fill-color': '#89b4fa',
                'fill-opacity': 0.15,
            }
        });
        map.addLayer({
            id: 'bbox-outline',
            type: 'line',
            source: 'bbox',
            paint: {
                'line-color': '#89b4fa',
                'line-width': 2,
            }
        });
        // Restore bbox from fields if present
        updateBboxFromFields();
    });

    const canvas = map.getCanvasContainer();

    canvas.addEventListener('mousedown', (e) => {
        if (!bboxDrawMode) return;
        if (e.button !== 0) return;
        e.preventDefault();
        bboxDrawing = true;
        bboxStart = map.unproject([e.offsetX, e.offsetY]);
        map.dragPan.disable();
    });

    canvas.addEventListener('mousemove', (e) => {
        if (!bboxDrawing || !bboxStart) return;
        const end = map.unproject([e.offsetX, e.offsetY]);
        updateBboxOverlay(bboxStart.lng, bboxStart.lat, end.lng, end.lat);
    });

    canvas.addEventListener('mouseup', (e) => {
        if (!bboxDrawing) return;
        bboxDrawing = false;
        map.dragPan.enable();
        // Exit draw mode after drawing
        bboxDrawMode = false;
        updateDrawBoxButton();
        canvas.style.cursor = '';
        if (!bboxStart) return;
        const end = map.unproject([e.offsetX, e.offsetY]);
        const west = Math.min(bboxStart.lng, end.lng).toFixed(4);
        const east = Math.max(bboxStart.lng, end.lng).toFixed(4);
        const south = Math.min(bboxStart.lat, end.lat).toFixed(4);
        const north = Math.max(bboxStart.lat, end.lat).toFixed(4);

        document.getElementById('bbox-west').value = west;
        document.getElementById('bbox-east').value = east;
        document.getElementById('bbox-south').value = south;
        document.getElementById('bbox-north').value = north;

        updateBboxOverlay(west, south, east, north);
        bboxStart = null;
    });
}

function toggleDrawBox() {
    bboxDrawMode = !bboxDrawMode;
    updateDrawBoxButton();
    if (map) {
        const canvas = map.getCanvasContainer();
        canvas.style.cursor = bboxDrawMode ? 'crosshair' : '';
    }
}

function updateDrawBoxButton() {
    const btn = document.getElementById('btn-draw-bbox');
    if (!btn) return;
    if (bboxDrawMode) {
        btn.classList.add('active');
        btn.textContent = 'Drawing... (drag to set area)';
    } else {
        btn.classList.remove('active');
        btn.textContent = 'Draw Box';
    }
}

function updateBboxOverlay(west, south, east, north) {
    if (!map || !map.getSource('bbox')) return;
    west = parseFloat(west);
    south = parseFloat(south);
    east = parseFloat(east);
    north = parseFloat(north);
    if (isNaN(west) || isNaN(south) || isNaN(east) || isNaN(north)) return;

    const geojson = {
        type: 'FeatureCollection',
        features: [{
            type: 'Feature',
            geometry: {
                type: 'Polygon',
                coordinates: [[
                    [west, south],
                    [east, south],
                    [east, north],
                    [west, north],
                    [west, south],
                ]]
            }
        }]
    };
    map.getSource('bbox').setData(geojson);
}

function bboxFieldChanged() {
    updateBboxFromFields();
}

function updateBboxFromFields() {
    const west = document.getElementById('bbox-west').value;
    const south = document.getElementById('bbox-south').value;
    const east = document.getElementById('bbox-east').value;
    const north = document.getElementById('bbox-north').value;
    if (west && south && east && north) {
        updateBboxOverlay(west, south, east, north);
    }
}

function getBbox() {
    const w = document.getElementById('bbox-west').value;
    const s = document.getElementById('bbox-south').value;
    const e = document.getElementById('bbox-east').value;
    const n = document.getElementById('bbox-north').value;
    if (w && s && e && n) {
        return w + ',' + s + ',' + e + ',' + n;
    }
    return null;
}

function updateBboxSummary() {
    const summary = document.getElementById('bbox-summary');
    const value = document.getElementById('bbox-summary-value');
    const bbox = getBbox();
    if (bbox) {
        const parts = bbox.split(',');
        value.textContent = parts[0] + ', ' + parts[1] + ' \u2192 ' + parts[2] + ', ' + parts[3];
        summary.classList.remove('warn');
    } else {
        value.textContent = 'No bbox set \u2014 go to Connect tab to draw one';
        summary.classList.add('warn');
    }
}

// ---------------------------------------------------------------------------
// Pipeline Cards
// ---------------------------------------------------------------------------

function toggleCard(card) {
    const wasExpanded = card.classList.contains('expanded');
    // Collapse all cards
    document.querySelectorAll('.source-card').forEach(c => {
        c.classList.remove('expanded');
        const body = c.querySelector('.card-body');
        if (body) body.classList.add('hidden');
    });
    // Expand clicked card if it wasn't already
    if (!wasExpanded) {
        card.classList.add('expanded');
        const body = card.querySelector('.card-body');
        if (body) body.classList.remove('hidden');
    }
}

function toggleChip(chip) {
    chip.classList.toggle('selected');
}

function getSelectedStates() {
    const chips = document.querySelectorAll('#noaa-states .chip.selected');
    return Array.from(chips).map(c => c.dataset.value).join(',');
}

// ---------------------------------------------------------------------------
// Pipeline API
// ---------------------------------------------------------------------------

async function startPipeline(name) {
    const bbox = getBbox();

    // Require bbox for all pipelines except import
    if (!bbox && name !== 'import') {
        log('Error: Draw a bounding box on the map before starting a pipeline.');
        const card = document.querySelector('[data-pipeline="' + name + '"]');
        if (card) {
            const statusEl = card.querySelector('[data-status="' + name + '"]');
            if (statusEl) {
                statusEl.className = 'card-status status status-error';
                statusEl.textContent = 'Draw a bounding box first';
            }
        }
        return;
    }

    const args = {};
    if (bbox) args.bbox = bbox;

    // Pipeline-specific args
    if (name === 'm2m') {
        args.m2m_username = document.getElementById('m2m-username').value;
        args.m2m_token = document.getElementById('m2m-token').value;
    } else if (name === 'sentinel') {
        args.api_key = document.getElementById('sentinel-key').value;
    } else if (name === 'import') {
        args.source = document.getElementById('import-source').value;
    }

    // Clear error-logged flag so a new failure gets logged
    const startCard = document.querySelector('[data-pipeline="' + name + '"]');
    if (startCard) delete startCard.dataset.errorLogged;

    try {
        const resp = await fetch('/api/pipelines/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken,
            },
            body: JSON.stringify({ pipeline: name, args }),
        });
        const data = await resp.json();
        if (resp.ok) {
            log('Started pipeline: ' + name);
            startPolling();
        } else {
            log('Failed to start ' + name + ': ' + (data.detail || JSON.stringify(data)));
        }
    } catch (e) {
        log('Error starting pipeline: ' + e.message);
    }
}

async function cancelPipeline(name) {
    try {
        const resp = await fetch('/api/pipelines/' + name + '/cancel', {
            method: 'POST',
            headers: { 'X-CSRF-Token': csrfToken },
        });
        const data = await resp.json();
        if (resp.ok) {
            log('Cancelled pipeline: ' + name);
        } else {
            log('Cancel failed: ' + (data.detail || JSON.stringify(data)));
        }
    } catch (e) {
        log('Error cancelling pipeline: ' + e.message);
    }
}

// ---------------------------------------------------------------------------
// Polling — Critical: NO DOM rebuild, only update existing elements
// ---------------------------------------------------------------------------

function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(pollStates, 2000);
    pollStates(); // immediate first poll
}

function stopPolling() {
    if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

async function pollStates() {
    try {
        const resp = await fetch('/api/pipelines/states');
        const states = await resp.json();
        let anyRunning = false;

        for (const [name, state] of Object.entries(states)) {
            if (!state) continue;

            const card = document.querySelector('[data-pipeline="' + name + '"]');
            if (!card) continue;

            const statusEl = card.querySelector('[data-status="' + name + '"]');
            const progressBar = card.querySelector('[data-progress="' + name + '"]');
            const progressFill = progressBar ? progressBar.querySelector('.progress-fill') : null;
            const btnStart = card.querySelector('.btn-start');
            const btnCancel = card.querySelector('.btn-cancel');

            const phase = state.phase || state.status || '';
            const pct = state.percent || (state.items_total ? Math.round(100 * (state.items_done || 0) / state.items_total) : 0);
            const msg = state.message || state.detail || state.error || '';

            if (phase === 'running' || phase === 'downloading' || phase === 'processing' || phase === 'starting' || phase === 'discovering' || phase === 'converting' || phase === 'merging' || phase === 'overviews' || phase === 'resolving') {
                anyRunning = true;
                if (progressBar) progressBar.classList.remove('hidden');
                if (progressFill) progressFill.style.width = pct + '%';
                if (btnStart) btnStart.classList.add('hidden');
                if (btnCancel) btnCancel.classList.remove('hidden');
                if (statusEl) {
                    statusEl.className = 'card-status status status-info';
                    statusEl.textContent = phase + (msg ? ': ' + msg : '') + ' (' + Math.round(pct) + '%)';
                }
            } else if (phase === 'cancelling') {
                anyRunning = true;
                if (progressBar) progressBar.classList.remove('hidden');
                if (progressFill) progressFill.className = 'progress-fill cancelling';
                if (btnStart) btnStart.classList.add('hidden');
                if (btnCancel) btnCancel.classList.add('hidden');
                if (statusEl) {
                    statusEl.className = 'card-status status status-warn';
                    statusEl.textContent = 'Cancelling...';
                }
            } else if (phase === 'done' || phase === 'complete') {
                if (progressBar) {
                    progressBar.classList.remove('hidden');
                    if (progressFill) {
                        progressFill.className = 'progress-fill green';
                        progressFill.style.width = '100%';
                    }
                }
                if (btnStart) btnStart.classList.remove('hidden');
                if (btnCancel) btnCancel.classList.add('hidden');
                if (statusEl) {
                    statusEl.className = 'card-status status status-ok';
                    statusEl.textContent = 'Complete' + (msg ? ': ' + msg : '');
                }
            } else if (phase === 'error' || phase === 'failed') {
                if (progressBar) progressBar.classList.add('hidden');
                if (progressFill) progressFill.className = 'progress-fill red';
                if (btnStart) btnStart.classList.remove('hidden');
                if (btnCancel) btnCancel.classList.add('hidden');
                if (statusEl) {
                    statusEl.className = 'card-status status status-error';
                    // Show last meaningful line of error on card
                    const errorLines = (msg || 'Unknown error').trim().split('\n').filter(l => l.trim());
                    const lastLine = errorLines[errorLines.length - 1] || 'Unknown error';
                    statusEl.textContent = 'Error: ' + lastLine.substring(0, 200);
                    statusEl.title = msg || '';  // full error on hover
                }
                // Log full error to console panel
                if (msg && !card.dataset.errorLogged) {
                    card.dataset.errorLogged = '1';
                    log('Pipeline ' + name + ' failed:\n' + msg);
                }
            } else if (phase === 'cancelled') {
                if (progressBar) progressBar.classList.add('hidden');
                if (btnStart) btnStart.classList.remove('hidden');
                if (btnCancel) btnCancel.classList.add('hidden');
                if (statusEl) {
                    statusEl.className = 'card-status status status-warn';
                    statusEl.textContent = 'Cancelled';
                }
            } else {
                // idle or unknown
                if (progressBar) progressBar.classList.add('hidden');
                if (btnStart) btnStart.classList.remove('hidden');
                if (btnCancel) btnCancel.classList.add('hidden');
                if (statusEl) {
                    statusEl.className = 'card-status';
                    statusEl.textContent = '';
                }
            }
        }

        if (!anyRunning && pollTimer) {
            stopPolling();
            refreshDisk();
        }
    } catch (e) {
        // Silently ignore poll errors
    }
}

// ---------------------------------------------------------------------------
// Transfer
// ---------------------------------------------------------------------------

function toggleAuthFields() {
    const method = document.querySelector('input[name="auth-method"]:checked').value;
    document.getElementById('auth-password-fields').classList.toggle('hidden', method !== 'password');
    document.getElementById('auth-key-fields').classList.toggle('hidden', method !== 'key');
    updateManualCommands();
}

async function testConnection() {
    const host = document.getElementById('transfer-host').value.trim();
    const username = document.getElementById('transfer-user').value.trim();
    const method = document.querySelector('input[name="auth-method"]:checked').value;
    const resultEl = document.getElementById('transfer-test-result');
    const indicatorEl = document.getElementById('transfer-method-indicator');

    if (!host || !username) {
        resultEl.className = 'status status-warn';
        resultEl.textContent = 'Enter hostname and username.';
        return;
    }

    const body = { host, username };
    if (method === 'password') {
        body.password = document.getElementById('transfer-password').value;
    } else {
        body.key_path = document.getElementById('transfer-keypath').value;
    }

    resultEl.className = 'status status-info';
    resultEl.textContent = 'Testing connection...';

    try {
        const resp = await fetch('/api/transfer/test', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken,
            },
            body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (data.ssh_ok) {
            const details = [];
            details.push('SSH: OK');
            details.push('rsync: ' + (data.rsync_available ? 'available' : 'not found'));
            details.push('Data dir writable: ' + (data.data_dir_writable ? 'yes' : 'no'));
            details.push('Docker: ' + (data.docker_ok ? 'OK' : 'not found'));
            if (data.disk_free_bytes) {
                details.push('Disk free: ' + formatSize(data.disk_free_bytes));
            }
            resultEl.className = 'status status-ok';
            resultEl.textContent = details.join(' | ');
            indicatorEl.className = 'status status-info';
            indicatorEl.textContent = 'Transfer method: ' + (data.transfer_method || 'sftp');
            document.getElementById('btn-transfer-all').disabled = false;
        } else {
            resultEl.className = 'status status-error';
            resultEl.textContent = 'SSH failed: ' + (data.error || 'unknown error');
            indicatorEl.textContent = '';
        }
    } catch (e) {
        resultEl.className = 'status status-error';
        resultEl.textContent = 'Connection test failed: ' + e.message;
    }
}

async function startTransfer() {
    const host = document.getElementById('transfer-host').value.trim();
    const username = document.getElementById('transfer-user').value.trim();
    const method = document.querySelector('input[name="auth-method"]:checked').value;

    const body = { host, username, auth_type: method };
    if (method === 'password') {
        body.password = document.getElementById('transfer-password').value;
    } else {
        body.key_path = document.getElementById('transfer-keypath').value;
    }

    const progressEl = document.getElementById('transfer-progress');
    progressEl.className = 'status status-info';
    progressEl.textContent = 'Transferring files...';
    log('Starting transfer to ' + host + '...');

    try {
        const resp = await fetch('/api/transfer/start', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken,
            },
            body: JSON.stringify(body),
        });
        const data = await resp.json();

        if (resp.ok) {
            const results = data.results || {};
            const succeeded = Object.values(results).filter(v => v).length;
            const total = Object.keys(results).length;
            progressEl.className = succeeded === total ? 'status status-ok' : 'status status-warn';
            progressEl.textContent = 'Transferred ' + succeeded + '/' + total + ' files.';
            log('Transfer complete: ' + succeeded + '/' + total + ' files');

            for (const [name, ok] of Object.entries(results)) {
                log('  ' + name + ': ' + (ok ? 'OK' : 'FAILED'));
            }
        } else {
            progressEl.className = 'status status-error';
            progressEl.textContent = data.detail || 'Transfer failed.';
            log('Transfer failed: ' + (data.detail || 'unknown error'));
        }
    } catch (e) {
        progressEl.className = 'status status-error';
        progressEl.textContent = 'Transfer error: ' + e.message;
        log('Transfer error: ' + e.message);
    }
}

async function deploy() {
    const host = document.getElementById('transfer-host').value.trim();
    const username = document.getElementById('transfer-user').value.trim();
    const method = document.querySelector('input[name="auth-method"]:checked').value;

    const body = { host, username };
    if (method === 'password') {
        body.password = document.getElementById('transfer-password').value;
    } else {
        body.key_path = document.getElementById('transfer-keypath').value;
    }

    log('Deploying to ' + host + '...');
    try {
        const resp = await fetch('/api/deploy', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrfToken,
            },
            body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (data.error) {
            log('Deploy error: ' + data.error);
        } else {
            log('Deploy complete. Registered: ' + (data.registered || []).join(', '));
            if (data.skipped && data.skipped.length) {
                log('Skipped (already registered): ' + data.skipped.join(', '));
            }
        }
    } catch (e) {
        log('Deploy error: ' + e.message);
    }
}

// ---------------------------------------------------------------------------
// Disk / File List — Uses safe DOM methods, no innerHTML
// ---------------------------------------------------------------------------

function buildFileList(files, container) {
    container.textContent = '';
    if (!files || files.length === 0) {
        const p = document.createElement('p');
        p.textContent = 'No files yet.';
        container.appendChild(p);
        return;
    }
    const list = document.createElement('div');
    list.className = 'file-list';
    for (const f of files) {
        const row = document.createElement('div');
        row.className = 'file-row';
        const nameSpan = document.createElement('span');
        nameSpan.className = 'file-name';
        nameSpan.textContent = f.name;
        const sizeSpan = document.createElement('span');
        sizeSpan.className = 'file-size';
        sizeSpan.textContent = formatSize(f.size);
        row.appendChild(nameSpan);
        row.appendChild(sizeSpan);
        list.appendChild(row);
    }
    container.appendChild(list);
}

async function refreshDisk() {
    try {
        const resp = await fetch('/api/disk');
        const data = await resp.json();

        // Update transfer file list using safe DOM methods
        const listEl = document.getElementById('transfer-file-list');
        buildFileList(data.files, listEl);

        // Disk stats
        const statsEl = document.getElementById('transfer-disk-stats');
        statsEl.textContent = '';
        const totalLabel = document.createTextNode('Total: ');
        const totalSpan = document.createElement('span');
        totalSpan.textContent = formatSize(data.total_size);
        const sep = document.createTextNode(' | Disk free: ');
        const freeSpan = document.createElement('span');
        freeSpan.textContent = formatSize(data.disk_free);
        statsEl.appendChild(totalLabel);
        statsEl.appendChild(totalSpan);
        statsEl.appendChild(sep);
        statsEl.appendChild(freeSpan);

        // Status tab summary cards
        document.getElementById('stat-disk-free').textContent = formatSize(data.disk_free);
        document.getElementById('stat-total-size').textContent = formatSize(data.total_size);

        // Status tab files
        const statusFiles = document.getElementById('status-files');
        const mbtFiles = (data.files || []).filter(f => f.name.endsWith('.mbtiles'));
        buildFileList(mbtFiles, statusFiles);
    } catch (e) {
        // silently ignore
    }
}

// ---------------------------------------------------------------------------
// Status Tab
// ---------------------------------------------------------------------------

async function refreshStatus() {
    try {
        const resp = await fetch('/api/pipelines/states');
        const states = await resp.json();

        let running = 0;
        let complete = 0;

        const container = document.getElementById('status-pipelines');
        container.textContent = '';

        const entries = Object.entries(states);
        if (entries.length === 0) {
            const p = document.createElement('p');
            p.textContent = 'No pipelines started.';
            container.appendChild(p);
        }

        for (const [name, state] of entries) {
            if (!state) continue;
            const phase = state.phase || state.status || 'idle';
            const pct = state.percent || 0;
            const msg = state.message || state.detail || '';

            if (phase === 'running' || phase === 'downloading' || phase === 'processing') running++;
            if (phase === 'done' || phase === 'complete') complete++;

            let statusClass = '';
            if (phase === 'done' || phase === 'complete') statusClass = 'status-ok';
            else if (phase === 'error' || phase === 'failed') statusClass = 'status-error';
            else if (phase === 'running' || phase === 'downloading' || phase === 'processing') statusClass = 'status-info';
            else if (phase === 'cancelling' || phase === 'cancelled') statusClass = 'status-warn';

            const row = document.createElement('div');
            row.className = 'status ' + statusClass;
            row.style.margin = '4px 0';

            const strong = document.createElement('strong');
            strong.textContent = name;
            row.appendChild(strong);

            let detail = ': ' + phase;
            if (pct > 0) detail += ' (' + Math.round(pct) + '%)';
            if (msg) detail += ' — ' + msg;
            row.appendChild(document.createTextNode(detail));

            container.appendChild(row);
        }

        document.getElementById('stat-running').textContent = running;
        document.getElementById('stat-complete').textContent = complete;

        if (running > 0) startPolling();
    } catch (e) {
        // silently ignore
    }
}

// ---------------------------------------------------------------------------
// Manual Commands
// ---------------------------------------------------------------------------

function updateManualCommands() {
    const host = document.getElementById('transfer-host').value.trim() || 'PI_HOST';
    const user = document.getElementById('transfer-user').value.trim() || 'administrator';

    const rsyncEl = document.getElementById('cmd-rsync');
    rsyncEl.textContent = 'rsync -avP ' + outputDir + '/*.mbtiles ' + user + '@' + host + ':/srv/geographica/data/';

    const deployEl = document.getElementById('cmd-deploy');
    deployEl.textContent = "ssh " + user + "@" + host + " 'cd /home/administrator/Code/geographica && docker compose restart tileserver'";
}

function copyCmd(id) {
    const el = document.getElementById(id);
    if (!el) return;
    navigator.clipboard.writeText(el.textContent).then(() => {
        const btn = el.parentElement.querySelector('.copy-btn');
        if (btn) {
            const orig = btn.textContent;
            btn.textContent = 'Copied!';
            setTimeout(() => btn.textContent = orig, 1500);
        }
    });
}

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

function log(msg) {
    const viewer = document.getElementById('log-viewer');
    if (!viewer) return;
    const ts = new Date().toLocaleTimeString();
    viewer.textContent += '[' + ts + '] ' + msg + '\n';
    viewer.scrollTop = viewer.scrollHeight;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function formatSize(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + ' ' + units[i];
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function estimatePipeline(name) {
    log('Estimate not yet implemented for ' + name);
}

document.addEventListener('DOMContentLoaded', init);

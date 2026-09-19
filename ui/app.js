let allRecommendations = [];
let allAuditLogs = [];
let currentSelectedRecId = null;

function getApiKey() {
    return sessionStorage.getItem('X-API-Key') || '';
}

async function apiFetch(url, options = {}) {
    options.headers = options.headers || {};
    const key = getApiKey();
    if (key) {
        options.headers['X-API-Key'] = key;
    }
    const response = await fetch(url, options);
    if (response.status === 401) {
        const userKey = prompt('X-API-Key authentication required. Please enter API Key:');
        if (userKey) {
            sessionStorage.setItem('X-API-Key', userKey);
            options.headers['X-API-Key'] = userKey;
            return fetch(url, options);
        }
    }
    return response;
}

document.addEventListener('DOMContentLoaded', () => {
    fetchDbInfo();
    fetchRecommendations();

    document.getElementById('filter-classification').addEventListener('change', applyFilters);
    document.getElementById('filter-action').addEventListener('change', applyFilters);
    document.getElementById('filter-status').addEventListener('change', applyFilters);
    document.getElementById('search-input').addEventListener('input', applyFilters);

    document.getElementById('override-reason-select').addEventListener('change', (e) => {
        const otherGroup = document.getElementById('other-reason-group');
        otherGroup.style.display = e.target.value === 'OTHER' ? 'flex' : 'none';
    });
});

async function fetchDbInfo() {
    try {
        const response = await apiFetch('/api/v1/db-info');
        if (response.ok) {
            const data = await response.json();
            const labelEl = document.getElementById('db-backend-label');
            if (labelEl && data.dialect) {
                const dialectName = data.dialect === 'postgresql' ? 'PostgreSQL' : (data.dialect === 'sqlite' ? 'SQLite' : data.dialect);
                labelEl.textContent = `${dialectName} Persistence`;
            }
        }
    } catch (err) {
        console.warn('Could not fetch DB info:', err);
    }
}


function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.style.display = 'none');

    if (tabName === 'recommendations') {
        document.getElementById('tab-btn-recs').classList.add('active');
        document.getElementById('content-recs').style.display = 'block';
    } else if (tabName === 'audit') {
        document.getElementById('tab-btn-audit').classList.add('active');
        document.getElementById('content-audit').style.display = 'block';
        fetchAuditLogs();
    }
}

async function fetchRecommendations() {
    try {
        const response = await apiFetch('/api/v1/recommendations');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        allRecommendations = await response.json();
        updateMetrics(allRecommendations);
        applyFilters();
    } catch (err) {
        console.error('Error fetching recommendations:', err);
        document.getElementById('table-body').innerHTML = `
            <tr>
                <td colspan="8" style="text-align: center; color: #ef4444; padding: 24px;">
                    Failed to load recommendations. Please ensure API server is running at localhost:8000.
                </td>
            </tr>
        `;
    }
}

function updateMetrics(recs) {
    document.getElementById('metric-total').textContent = recs.length;
    document.getElementById('metric-transition').textContent = recs.filter(r => r.recommended_action === 'TRANSITION').length;
    document.getElementById('metric-delete').textContent = recs.filter(r => r.recommended_action === 'DELETE').length;
    document.getElementById('metric-confirmed').textContent = recs.filter(r => r.approval_status === 'confirmed').length;
    document.getElementById('metric-overridden').textContent = recs.filter(r => r.approval_status === 'overridden').length;
}

function applyFilters() {
    const classification = document.getElementById('filter-classification').value;
    const action = document.getElementById('filter-action').value;
    const statusVal = document.getElementById('filter-status').value;
    const search = document.getElementById('search-input').value.toLowerCase().trim();

    let filtered = allRecommendations.filter(r => {
        if (classification !== 'ALL' && r.evidence_snapshot.data_classification !== classification) return false;
        if (action !== 'ALL' && r.recommended_action !== action) return false;
        if (statusVal !== 'ALL' && r.approval_status !== statusVal) return false;
        if (search) {
            const matchesId = r.object_id.toLowerCase().includes(search);
            const matchesBucket = (r.evidence_snapshot.bucket_or_account || '').toLowerCase().includes(search);
            if (!matchesId && !matchesBucket) return false;
        }
        return true;
    });

    renderTable(filtered);
}

function renderTable(recommendations) {
    const tbody = document.getElementById('table-body');
    if (recommendations.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align: center; color: #94a3b8; padding: 24px;">
                    No objects matching the current filters.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = recommendations.map((r, idx) => {
        const ev = r.evidence_snapshot;
        const targetClass = r.target_storage_class ? ` ➔ <b>${r.target_storage_class}</b>` : '';
        const ruleBadges = r.triggering_rules.map(rule => `<span class="rule-tag">${rule}</span>`).join('');
        
        const confBadge = `<span class="badge badge-conf-${r.confidence_tier}">
            ${r.confidence_tier} ${r.confidence_tier === 'LOW' ? '⚠️' : '✓'}
        </span>`;

        const statusBadge = `<span class="badge badge-status-${r.approval_status}">${r.approval_status.toUpperCase()}</span>`;

        let periodicTag = '';
        if (r.requires_periodic_review) {
            periodicTag = `<div style="margin-top: 4px;"><span class="badge" style="background: rgba(2, 132, 199, 0.2); color: #38bdf8; border: 1px solid #0284c7;">COMPLIANCE REVIEW REQ</span></div>`;
        }

        return `
            <tr>
                <td>
                    <div><strong>${r.object_id}</strong></div>
                    <div style="font-size: 0.72rem; color: #94a3b8;">${ev.cloud_provider} | ${ev.data_classification}</div>
                </td>
                <td>
                    <div>${r.current_class}${targetClass}</div>
                    <div style="font-size: 0.72rem; color: #94a3b8;">Age: ${ev.object_age_days}d</div>
                </td>
                <td>
                    <span class="badge badge-action-${r.recommended_action}">${r.recommended_action}</span>
                </td>
                <td>
                    <span class="badge-impact-${r.impact_tier}">${r.impact_tier}</span>
                </td>
                <td>${confBadge}</td>
                <td>
                    ${statusBadge}
                    ${periodicTag}
                </td>
                <td>${ruleBadges}</td>
                <td>
                    <div class="action-btn-group">
                        <button class="btn-sm btn-evidence" onclick="showEvidenceModal('${r.id}')">Evidence</button>
                        ${r.approval_status === 'pending' ? `
                            <button class="btn-sm btn-confirm" onclick="openConfirmModal('${r.id}')">Confirm</button>
                            <button class="btn-sm btn-override" onclick="openOverrideModal('${r.id}')">Override</button>
                        ` : `
                            <button class="btn-sm btn-rollback" onclick="openRollbackModal('${r.id}')">Rollback</button>
                        `}
                        ${r.requires_periodic_review ? `
                            <button class="btn-sm btn-review" onclick="openReviewModal('${r.id}')">Review</button>
                        ` : ''}
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

// --- GOVERNANCE MODAL HANDLERS ---

function showEvidenceModal(recId) {
    const rec = allRecommendations.find(r => r.id === recId || r.object_id === recId);
    if (!rec) return;

    document.getElementById('modal-object-id').textContent = rec.object_id;
    document.getElementById('modal-summary').textContent = rec.reasoning_summary;
    document.getElementById('modal-json').textContent = JSON.stringify(rec, null, 2);
    document.getElementById('evidence-modal').style.display = 'flex';
}

function openConfirmModal(recId) {
    currentSelectedRecId = recId;
    document.getElementById('confirm-reviewer-id').value = 'usr-compliance-admin';
    document.getElementById('confirm-modal').style.display = 'flex';
}

async function submitConfirm() {
    const reviewerId = document.getElementById('confirm-reviewer-id').value.trim();
    if (!reviewerId) {
        alert('Please enter a valid Reviewer ID');
        return;
    }

    try {
        const response = await apiFetch(`/api/v1/recommendations/${currentSelectedRecId}/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reviewer_id: reviewerId })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Confirmation failed');
        }

        closeModal();
        fetchRecommendations();
    } catch (err) {
        alert(`Error confirming recommendation: ${err.message}`);
    }
}

function openOverrideModal(recId) {
    currentSelectedRecId = recId;
    document.getElementById('override-reviewer-id').value = 'usr-compliance-admin';
    document.getElementById('override-reason-select').value = 'PENDING_CLINICAL_TRIAL';
    document.getElementById('other-reason-text').value = '';
    document.getElementById('other-reason-group').style.display = 'none';
    document.getElementById('override-modal').style.display = 'flex';
}

async function submitOverride() {
    const reviewerId = document.getElementById('override-reviewer-id').value.trim();
    const reason = document.getElementById('override-reason-select').value;
    const otherText = document.getElementById('other-reason-text').value.trim();

    if (!reviewerId) {
        alert('Please enter a valid Reviewer ID');
        return;
    }

    if (reason === 'OTHER' && !otherText) {
        alert('Detailed explanation text is required when override reason is OTHER');
        return;
    }

    try {
        const response = await apiFetch(`/api/v1/recommendations/${currentSelectedRecId}/override`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                reviewer_id: reviewerId,
                override_reason: reason,
                other_reason_text: reason === 'OTHER' ? otherText : null
            })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Override failed');
        }

        closeModal();
        fetchRecommendations();
    } catch (err) {
        alert(`Error overriding recommendation: ${err.message}`);
    }
}

function openRollbackModal(recId) {
    currentSelectedRecId = recId;
    document.getElementById('rollback-reviewer-id').value = 'usr-audit-lead';
    document.getElementById('rollback-reason-text').value = 'Reverting recommendation approval status due to revised audit policy.';
    document.getElementById('rollback-modal').style.display = 'flex';
}

async function submitRollback() {
    const reviewerId = document.getElementById('rollback-reviewer-id').value.trim();
    const reason = document.getElementById('rollback-reason-text').value.trim();

    if (!reviewerId || !reason) {
        alert('Please fill out Reviewer ID and Rollback Reason');
        return;
    }

    try {
        const response = await apiFetch(`/api/v1/recommendations/${currentSelectedRecId}/rollback`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reviewer_id: reviewerId, reason: reason })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Rollback failed');
        }

        closeModal();
        fetchRecommendations();
    } catch (err) {
        alert(`Error executing rollback: ${err.message}`);
    }
}

function openReviewModal(recId) {
    currentSelectedRecId = recId;
    document.getElementById('review-reviewer-id').value = 'usr-hipaa-officer';
    document.getElementById('review-modal').style.display = 'flex';
}

async function submitReview() {
    const reviewerId = document.getElementById('review-reviewer-id').value.trim();
    if (!reviewerId) {
        alert('Please enter a valid Reviewer ID');
        return;
    }

    try {
        const response = await apiFetch(`/api/v1/recommendations/${currentSelectedRecId}/periodic-review`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reviewer_id: reviewerId })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || 'Periodic review submission failed');
        }

        closeModal();
        fetchRecommendations();
    } catch (err) {
        alert(`Error submitting periodic review: ${err.message}`);
    }
}

function closeModal() {
    document.querySelectorAll('.modal-backdrop').forEach(m => m.style.display = 'none');
}

// --- AUDIT TRAIL LOG TAB FUNCTIONS ---

async function fetchAuditLogs() {
    try {
        const response = await apiFetch('/api/v1/audit-log');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        allAuditLogs = await response.json();
        renderAuditTable(allAuditLogs);
    } catch (err) {
        console.error('Error fetching audit logs:', err);
        document.getElementById('audit-table-body').innerHTML = `
            <tr>
                <td colspan="6" style="text-align: center; color: #ef4444; padding: 24px;">
                    Failed to load audit trail logs.
                </td>
            </tr>
        `;
    }
}

function renderAuditTable(logs) {
    const tbody = document.getElementById('audit-table-body');
    if (logs.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" style="text-align: center; color: #94a3b8; padding: 24px;">
                    No audit log entries recorded yet.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = logs.map(l => {
        const dateStr = new Date(l.timestamp).toLocaleString();
        const shortHash = l.entry_hash ? l.entry_hash.substring(0, 12) + '...' : 'N/A';
        const prevHash = l.previous_hash ? l.previous_hash.substring(0, 12) + '...' : 'N/A';
        const detailsStr = JSON.stringify(l.details);

        return `
            <tr>
                <td><strong>#${l.id}</strong></td>
                <td>${dateStr}</td>
                <td><span class="badge" style="background: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid #0284c7;">${l.event_type}</span></td>
                <td>${l.actor}</td>
                <td>${l.object_id || 'N/A'}</td>
                <td>
                    <div style="font-family: monospace; font-size: 0.72rem; color: #4ade80;">Hash: ${shortHash}</div>
                    <div style="font-family: monospace; font-size: 0.72rem; color: #94a3b8;">Prev: ${prevHash}</div>
                    <div style="font-size: 0.72rem; color: #cbd5e1; margin-top: 2px;">${detailsStr}</div>
                </td>
            </tr>
        `;
    }).join('');
}

async function verifyAuditChain() {
    const statusDiv = document.getElementById('audit-verify-status');
    statusDiv.style.display = 'block';
    statusDiv.className = 'badge';
    statusDiv.style.background = 'rgba(2, 132, 199, 0.2)';
    statusDiv.style.color = '#38bdf8';
    statusDiv.textContent = 'Verifying SHA-256 cryptographic hash chain integrity across all entries...';

    try {
        const response = await apiFetch('/api/v1/audit-log/verify');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const result = await response.json();

        if (result.is_valid) {
            statusDiv.style.background = 'rgba(34, 197, 94, 0.2)';
            statusDiv.style.color = '#4ade80';
            statusDiv.style.border = '1px solid #22c55e';
            statusDiv.innerHTML = `✓ <strong>VALID AUDIT CHAIN</strong>: ${result.message}`;
        } else {
            statusDiv.style.background = 'rgba(239, 68, 68, 0.2)';
            statusDiv.style.color = '#f87171';
            statusDiv.style.border = '1px solid #ef4444';
            statusDiv.innerHTML = `⚠️ <strong>AUDIT TAMPERING DETECTED</strong>: ${result.message}`;
        }
    } catch (err) {
        statusDiv.style.background = 'rgba(239, 68, 68, 0.2)';
        statusDiv.style.color = '#f87171';
        statusDiv.textContent = `Error verifying chain: ${err.message}`;
    }
}

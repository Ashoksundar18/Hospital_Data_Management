let allRecommendations = [];

document.addEventListener('DOMContentLoaded', () => {
    fetchRecommendations();

    document.getElementById('filter-classification').addEventListener('change', applyFilters);
    document.getElementById('filter-action').addEventListener('change', applyFilters);
    document.getElementById('filter-data').addEventListener('change', applyFilters);
    document.getElementById('search-input').addEventListener('input', applyFilters);
});

async function fetchRecommendations() {
    try {
        const response = await fetch('/api/v1/recommendations');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        allRecommendations = await response.json();
        updateMetrics(allRecommendations);
        applyFilters();
    } catch (err) {
        console.error('Error fetching recommendations:', err);
        document.getElementById('table-body').innerHTML = `
            <tr>
                <td colspan="7" style="text-align: center; color: #ef4444; padding: 24px;">
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
    document.getElementById('metric-protected').textContent = recs.filter(r => r.triggering_rules.includes('RULE_LEGAL_HOLD') || r.triggering_rules.includes('RULE_RETENTION_LOCK')).length;
    document.getElementById('metric-missing').textContent = recs.filter(r => r.confidence_tier === 'LOW').length;
}

function applyFilters() {
    const classification = document.getElementById('filter-classification').value;
    const action = document.getElementById('filter-action').value;
    const dataFilter = document.getElementById('filter-data').value;
    const search = document.getElementById('search-input').value.toLowerCase().trim();

    let filtered = allRecommendations.filter(r => {
        if (classification !== 'ALL' && r.evidence_snapshot.data_classification !== classification) {
            return false;
        }
        if (action !== 'ALL' && r.recommended_action !== action) {
            return false;
        }
        if (dataFilter === 'MISSING' && r.confidence_tier !== 'LOW') {
            return false;
        }
        if (dataFilter === 'COMPLETE' && r.confidence_tier !== 'HIGH') {
            return false;
        }
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
                <td colspan="7" style="text-align: center; color: #94a3b8; padding: 24px;">
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

        return `
            <tr>
                <td>
                    <div><strong>${r.object_id}</strong></div>
                    <div style="font-size: 0.75rem; color: #94a3b8;">${ev.cloud_provider} | ${ev.data_classification}</div>
                </td>
                <td>
                    <div>${r.current_class}${targetClass}</div>
                    <div style="font-size: 0.75rem; color: #94a3b8;">Age: ${ev.object_age_days}d</div>
                </td>
                <td>
                    <span class="badge badge-action-${r.recommended_action}">${r.recommended_action}</span>
                </td>
                <td>
                    <span class="badge-impact-${r.impact_tier}">${r.impact_tier}</span>
                </td>
                <td>${confBadge}</td>
                <td>${ruleBadges}</td>
                <td>
                    <button class="btn-evidence" onclick="showEvidenceModal(${idx}, '${r.object_id}')">Evidence</button>
                </td>
            </tr>
        `;
    }).join('');
}

function showEvidenceModal(idx, objectId) {
    const rec = allRecommendations.find(r => r.object_id === objectId);
    if (!rec) return;

    document.getElementById('modal-object-id').textContent = rec.object_id;
    document.getElementById('modal-summary').textContent = rec.reasoning_summary;
    document.getElementById('modal-json').textContent = JSON.stringify(rec, null, 2);

    document.getElementById('evidence-modal').style.display = 'flex';
}

function closeModal() {
    document.getElementById('evidence-modal').style.display = 'none';
}

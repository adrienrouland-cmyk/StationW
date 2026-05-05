// State
let currentOrders = [];
let currentRequestForModal = null;

// Initialization
document.addEventListener('DOMContentLoaded', () => {
    fetchOrders();
    fetchClients();
});

// View switching
function switchView(viewId) {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.querySelectorAll('nav a').forEach(a => a.classList.remove('active'));
    
    document.getElementById(`view-${viewId}`).classList.add('active');
    document.getElementById(`nav-${viewId}`).classList.add('active');
    
    document.getElementById('page-title').textContent = viewId === 'orders' ? 'Orders Management' : 'Clients Directory';
}

// Fetch Clients
async function fetchClients() {
    try {
        const response = await fetch('/api/dashboard/clients');
        const data = await response.json();
        renderClients(data.clients);
    } catch (error) {
        console.error('Error fetching clients:', error);
    }
}

function renderClients(clients) {
    const tbody = document.querySelector('#clients-table tbody');
    tbody.innerHTML = '';
    
    clients.forEach(c => {
        const scoreClass = c.client_score >= 0.8 ? 'score-high' : 'score-low';
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${c.client_id}</td>
            <td><strong>${c.company_name}</strong></td>
            <td><span class="score-badge ${scoreClass}">${c.client_score}</span></td>
        `;
        tbody.appendChild(tr);
    });
}

// Fetch Orders
async function fetchOrders() {
    try {
        const response = await fetch('/api/dashboard/orders');
        const data = await response.json();
        currentOrders = data.orders;
        renderOrders(currentOrders);
    } catch (error) {
        console.error('Error fetching orders:', error);
    }
}

function renderOrders(orders) {
    const tbody = document.querySelector('#orders-table tbody');
    tbody.innerHTML = '';
    
    orders.forEach(o => {
        const tr = document.createElement('tr');
        
        // Format Status
        let displayStatus = o.status.replace(/_/g, ' ');
        const badge = `<span class="badge status-${o.status}">${displayStatus}</span>`;
        
        // Actions
        let actions = '';
        if (o.status === 'negotiating') {
            actions = `<button class="btn btn-primary" onclick="openNegotiationModal('${o.request_id}')">Review & Send Quote</button>`;
        } else if (o.status === 'ready_for_quote') {
             actions = `<button class="btn btn-primary" onclick="sendQuote('${o.request_id}')">Send Quote</button>`;
        } else if (o.status === 'quote_sent') {
            actions = `<a href="/request/${o.request_id}/quote" target="_blank" class="btn btn-secondary">Download PDF</a>`;
        }
        
        tr.innerHTML = `
            <td><small>${o.request_id.split('_')[1] || o.request_id}</small></td>
            <td>${new Date(o.created_at).toLocaleDateString()}</td>
            <td>
                <div>${o.delivery_country || 'Unknown'}</div>
                <small class="text-muted">${o.order_text.substring(0, 50)}...</small>
            </td>
            <td>${badge}</td>
            <td>${actions}</td>
        `;
        tbody.appendChild(tr);
    });
}

// Negotiation Modal Logic
function openNegotiationModal(requestId) {
    const order = currentOrders.find(o => o.request_id === requestId);
    if (!order || !order.proposals) return;
    
    currentRequestForModal = requestId;
    const content = document.getElementById('modal-content');
    content.innerHTML = '';
    
    order.proposals.forEach((p, index) => {
        const div = document.createElement('div');
        div.className = 'proposal-item';
        div.innerHTML = `
            <h4>${p.product} (Line ${p.line_id})</h4>
            <div class="text-muted" style="margin-bottom: 12px;">
                Requested Qty: ${p.requested_quantity} (Stock: ${p.available_quantity})<br>
                Wanted Price: ${p.wanted_unit_price} (Stock Price: ${p.stock_unit_price})
            </div>
            
            <div class="input-group">
                <div class="input-wrapper">
                    <label>Offered Quantity</label>
                    <input type="number" id="offer-qty-${index}" value="${p.proposal.offer_quantity}">
                </div>
                <div class="input-wrapper">
                    <label>Offered Unit Price (€)</label>
                    <input type="number" step="0.01" id="offer-price-${index}" value="${p.proposal.offer_unit_price}">
                </div>
            </div>
        `;
        content.appendChild(div);
    });
    
    document.getElementById('negotiating-modal').classList.add('active');
}

function closeModal() {
    document.getElementById('negotiating-modal').classList.remove('active');
    currentRequestForModal = null;
}

async function saveAndSendQuote() {
    if (!currentRequestForModal) return;
    
    const order = currentOrders.find(o => o.request_id === currentRequestForModal);
    
    // Gather updated proposals
    const updatedProposals = order.proposals.map((p, index) => {
        return {
            line_id: p.line_id,
            proposal: {
                offer_quantity: parseFloat(document.getElementById(`offer-qty-${index}`).value),
                offer_unit_price: parseFloat(document.getElementById(`offer-price-${index}`).value)
            }
        };
    });
    
    try {
        // 1. Update lines
        await fetch(`/api/dashboard/orders/${currentRequestForModal}/update-lines`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proposals: updatedProposals })
        });
        
        // 2. Send Quote
        await sendQuote(currentRequestForModal);
        
        closeModal();
    } catch (error) {
        console.error('Error saving and sending quote:', error);
        alert('An error occurred. Check console.');
    }
}

async function sendQuote(requestId) {
    try {
        const btn = event ? event.target : null;
        if (btn) btn.textContent = 'Sending...';
        
        const res = await fetch(`/api/dashboard/orders/${requestId}/send-quote`, { method: 'POST' });
        const data = await res.json();
        
        if (data.success) {
            await fetchOrders(); // refresh view
        } else {
            alert('Failed to send quote');
        }
    } catch (error) {
        console.error('Error sending quote:', error);
    }
}

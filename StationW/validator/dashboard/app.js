let currentOrders = [];
let currentClients = [];
let currentProducts = [];
let currentRequestForModal = null;
let currentView = 'dashboard';

const STATUS_LABELS = {
    pending: 'Pending',
    still_getting_info: 'Still getting info',
    negotiating: 'Negotiating',
    quote_sent: 'Quote sent',
    canceled: 'Canceled',
    error: 'Error',
};

document.addEventListener('DOMContentLoaded', () => {
    fetchOrders();
    fetchClients();
    fetchProducts();
    const tabs = document.getElementById('tabs');
    if (tabs) {
        tabs.addEventListener('click', handleDealsTabClick);
    }
    const primaryAction = document.getElementById('primary-action-btn');
    if (primaryAction) {
        primaryAction.addEventListener('click', () => {
            if (currentView === 'products') {
                toast('New product action opened');
            } else {
                toast('New request action opened');
            }
        });
    }
});

function esc(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatDate(value) {
    if (!value) return '—';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleDateString();
}

function formatOrderId(requestId) {
    if (!requestId) return '—';
    return requestId.split('_')[1] || requestId;
}

function getStatusClass(status) {
    return `status-${status || 'pending'}`;
}

function getClientScoreClass(score) {
    if (score >= 0.8) return 'score-high';
    if (score >= 0.6) return 'score-medium';
    return 'score-low';
}

const INI_COLORS = ['#3b5bff', '#16a373', '#e89c2a', '#6b3df3', '#d04848', '#0ea5e9', '#0f766e', '#db2777'];

function iniColor(name) {
    let hash = 0;
    const value = String(name || '');
    for (let index = 0; index < value.length; index += 1) {
        hash = (hash * 31 + value.charCodeAt(index)) & 0xfffffff;
    }
    return INI_COLORS[hash % INI_COLORS.length];
}

function initials(name) {
    return String(name || '')
        .split(/\s+/)
        .map(part => part[0])
        .slice(0, 2)
        .join('')
        .toUpperCase() || 'SW';
}

const CHANNELS = {
    email: 'Email',
    whats: 'WhatsApp',
    voice: 'Voice memo',
    sms: 'SMS',
    pdf: 'PDF',
    excel: 'Excel',
};

const CHANNEL_ICONS = {
    email: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="m2 7 10 7L22 7"/></svg>`,
    whats: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.5 8.5 0 1 1-3.6-6.96"/><path d="M14 8.5a4 4 0 0 1 4 4"/><path d="m21 4-4 4-2-2"/><path d="M4 21l1.7-4.4A8.5 8.5 0 0 1 9 4.5"/></svg>`,
    voice: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 19v3"/></svg>`,
    sms: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>`,
    pdf: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M9 13h1.5a1.5 1.5 0 0 1 0 3H9zM14 13h2v3M14 13v6"/></svg>`,
    excel: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="m8 9 8 6M8 15l8-6"/></svg>`,
};

function chBadge(key) {
    const channel = CHANNELS[key] ? key : 'email';
    return `<span class="ch ${channel}" title="${CHANNELS[channel]}">${CHANNEL_ICONS[channel]}</span>`;
}

function scoreCell(score) {
    const value = Number(score) || 0;
    let cls = '';
    let tier = 'A';
    if (value >= 80) {
        cls = '';
        tier = 'A';
    } else if (value >= 60) {
        cls = 'med';
        tier = 'B';
    } else {
        cls = 'low';
        tier = 'C';
    }
    return `<div class="score ${cls}"><span class="num">${value}</span><span class="bar"><i style="width:${Math.max(0, Math.min(100, value))}%"></i></span><span class="tier">${tier}</span></div>`;
}

function setText(id, value) {
    const element = document.getElementById(id);
    if (element) {
        element.textContent = value;
    }
}

function updateSummary() {
    const counts = currentOrders.reduce((acc, order) => {
        acc.total += 1;
        acc[order.status] = (acc[order.status] || 0) + 1;
        return acc;
    }, { total: 0 });

    const quoteSentCount = counts.quote_sent || 0;
    const negotiatingCount = counts.negotiating || 0;
    const waitingForInputCount = counts.still_getting_info || 0;
    const negotiationDealCount = negotiatingCount + quoteSentCount;

    setText('stat-total-orders', counts.total);
    setText('stat-ready', waitingForInputCount);
    setText('stat-negotiating', negotiatingCount);
    setText('stat-quoted', quoteSentCount);
    setText('nav-dashboard-count', counts.total);
    setText('nav-deals-count', negotiationDealCount);
    setText('nav-clients-count', currentClients.length);
    if (currentView === 'dashboard') {
        setText('orders-count-pill', `${counts.total} items`);
        setText('attention-meta', `${negotiatingCount + waitingForInputCount + (counts.error || 0)} orders`);
        setText('helper-primary', `${negotiationDealCount} open requests`);
    }

    if (currentView === 'dashboard') {
        renderOverview(counts);
        renderActionList();
    }
    renderDeals();
}

function switchView(viewId) {
    currentView = viewId;
    document.querySelectorAll('.view').forEach(view => view.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));

    const dashboardKpis = document.querySelector('.kpis');
    if (dashboardKpis) {
        dashboardKpis.style.display = viewId === 'dashboard' ? '' : 'none';
    }

    const sidePanel = document.querySelector('.side-panel');
    if (sidePanel) {
        sidePanel.style.display = viewId === 'dashboard' ? '' : 'none';
    }

    const panelActions = document.querySelector('.panel-actions');
    if (panelActions) {
        panelActions.style.display = viewId === 'dashboard' ? '' : 'none';
    }

    const targetView = document.getElementById(`view-${viewId}`);
    const targetNav = document.getElementById(`nav-${viewId}`);

    if (targetView) {
        targetView.classList.add('active');
    }

    if (targetNav) {
        targetNav.classList.add('active');
    }

    const titleMap = {
        dashboard: ['Dashboard', 'Dashboard', 'Where chaos becomes decisions - and decisions become revenue.'],
        deals: ['Deals', 'Deals', 'All requests turned into deals - grouped by what they need from you.'],
        products: ['Products', 'Products', 'Live inventory sourced from database/products.csv.'],
        clients: ['Customers', 'Customers', 'Client scores and company records in the same operational workspace.'],
    };
    const [crumb, title, subtitle] = titleMap[viewId] || titleMap.dashboard;
    document.getElementById('crumb-view').textContent = crumb;
    document.getElementById('page-title').textContent = title;
    document.getElementById('page-subtitle').textContent = subtitle;
    document.getElementById('orders-panel-title').textContent = title;
    document.getElementById('orders-panel-subtitle').textContent = viewId === 'dashboard'
        ? 'Live operational queue'
        : viewId === 'deals'
            ? 'Deal pipeline and negotiation board'
            : viewId === 'products'
                ? 'Inventory sourced from CSV'
                : 'Client directory';

    if (viewId === 'dashboard') {
        const counts = currentOrders.reduce((acc, order) => {
            acc.total += 1;
            acc[order.status] = (acc[order.status] || 0) + 1;
            return acc;
        }, { total: 0 });
        const readyCount = (counts.quote_sent || 0);
        setText('helper-primary', `${readyCount} open requests`);
        setText('orders-count-pill', `${counts.total} items`);
    } else if (viewId === 'deals') {
        setText('helper-primary', `${currentOrders.length} deals`);
    } else if (viewId === 'products') {
        setText('helper-primary', `${currentProducts.length} products`);
    } else if (viewId === 'clients') {
        setText('helper-primary', `${currentClients.length} customers`);
    }

    const actionButton = document.getElementById('primary-action-btn');
    if (actionButton) {
        actionButton.textContent = viewId === 'products' ? 'New product' : 'New request';
    }

    document.querySelectorAll('.helper-bar .seg button').forEach(button => button.classList.remove('on'));
    const activeButton = Array.from(document.querySelectorAll('.helper-bar .seg button')).find(button => button.textContent.trim() === (viewId === 'clients' ? 'Customers' : viewId === 'deals' ? 'Deals' : viewId === 'products' ? 'Products' : 'Dashboard'));
    if (activeButton) {
        activeButton.classList.add('on');
    }
}

async function fetchClients() {
    try {
        const response = await fetch('/api/dashboard/clients');
        const data = await response.json();
        currentClients = data.clients || [];
        renderClients(currentClients);
        updateSummary();
    } catch (error) {
        console.error('Error fetching clients:', error);
    }
}

async function fetchProducts() {
    try {
        const response = await fetch('/api/dashboard/products');
        const data = await response.json();
        currentProducts = data.products || [];
        renderProducts(currentProducts);
    } catch (error) {
        console.error('Error fetching products:', error);
    }
}

function renderClients(clients) {
    const tbody = document.querySelector('#clients-table tbody');
    if (!tbody) return;

    if (!clients.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No clients available.</td></tr>';
        return;
    }

    tbody.innerHTML = clients.map(client => `
        <tr>
            <td class="client-id">${esc(client.client_id)}</td>
            <td>
                <div class="client-cell">
                    <div class="ini" style="background:#3b5bff">${esc((client.company_name || 'CL').split(/\s+/).map(part => part[0]).slice(0, 2).join('').toUpperCase())}</div>
                    <div>
                        <div class="nm">${esc(client.company_name)}</div>
                        <div class="id">${esc(client.client_id)}</div>
                    </div>
                </div>
            </td>
            <td><span class="score-badge ${getClientScoreClass(client.client_score)}">${Number(client.client_score).toFixed(2)}</span></td>
            <td><button class="btn sm" data-client-id="${esc(client.client_id)}" onclick="showClientOrders('${esc(client.client_id)}')">Orders</button></td>
        </tr>
    `).join('');
}

function renderProducts(products) {
    const tbody = document.querySelector('#products-table tbody');
    if (!tbody) return;

    if (!products.length) {
        tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No products available.</td></tr>';
        return;
    }

    tbody.innerHTML = products.map(product => {
        const price = Number(product.price_per_unit || 0);
        const quantity = product.quantity_available ?? '';
        const moq = product.moq ?? '';
        const refill = product.refill_time ?? '';
        const status = String(product.status || 'active').toLowerCase();
        return `
            <tr>
                <td class="client-id">${esc(product.sku_code || product.sku || '—')}</td>
                <td>
                    <div class="order-cell">
                        <div>
                            <div class="summary">${esc(product.product_name || product.name || 'Unnamed product')}</div>
                            <div class="sub">${esc(product.description || '')}</div>
                        </div>
                    </div>
                </td>
                <td>${esc(product.brand || '—')}</td>
                <td>${esc(product.category || '—')}</td>
                <td>${esc(product.unit || '—')}</td>
                <td>${price ? `€${price.toFixed(2)}` : '—'}</td>
                <td>${esc(String(quantity))}</td>
                <td>${esc(String(moq))}</td>
                <td>${esc(String(refill))}d</td>
                <td><span class="badge ${status === 'active' ? 'status-paid' : 'status-pending'}">${esc(status)}</span></td>
            </tr>
        `;
    }).join('');
}

async function fetchOrders() {
    try {
        const response = await fetch('/api/dashboard/orders');
        const data = await response.json();
        currentOrders = data.orders || [];
        renderOrders(currentOrders);
        updateSummary();
    } catch (error) {
        console.error('Error fetching orders:', error);
    }
}

function renderOrders(orders) {
    const tbody = document.querySelector('#orders-table tbody');
    if (!tbody) return;

    if (!orders.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No orders available.</td></tr>';
        return;
    }

    tbody.innerHTML = orders.map(order => {
        const statusLabel = STATUS_LABELS[order.status] || order.status.replace(/_/g, ' ');
        const summary = esc(order.delivery_country || 'Unknown');
        const details = esc((order.order_text || '').substring(0, 60));
        let actions = '';

        if (order.status === 'negotiating') {
            actions = `<button class="btn primary sm" type="button" onclick="openNegotiationModal('${order.request_id}')">Review & Send Quote</button>`;
        } else if (order.status === 'quote_sent') {
            actions = `<span class="pill yes">Quote sent</span>`;
        } else if (order.status === 'still_getting_info') {
            actions = `<button class="btn sm" type="button" onclick="openDealDetails('${order.request_id}')">Review</button>`;
        } else {
            actions = `<span class="text-muted">—</span>`;
        }

        return `
            <tr>
                <td>
                    <div class="order-cell">
                        <div class="mono">${esc(formatOrderId(order.request_id))}</div>
                    </div>
                </td>
                <td>${esc(formatDate(order.created_at))}</td>
                <td>
                    <div class="order-cell">
                        <div>
                            <div class="summary">${summary}</div>
                            <div class="sub">${details || 'No description available'}${(order.order_text || '').length > 60 ? '…' : ''}</div>
                        </div>
                    </div>
                </td>
                <td><span class="badge ${getStatusClass(order.status)}">${esc(statusLabel)}</span></td>
                <td>${actions}</td>
            </tr>
        `;
    }).join('');
}

function renderActionList() {
    const list = document.getElementById('action-list');
    if (!list) return;

    const items = currentOrders.filter(order => ['negotiating', 'still_getting_info', 'error'].includes(order.status)).slice(0, 5);

    if (!items.length) {
        list.innerHTML = '<div class="empty-state">No orders need attention right now.</div>';
        return;
    }

    list.innerHTML = items.map(order => {
        let action = '<span class="pill grey">Review</span>';
        if (order.status === 'negotiating') {
            action = `<button class="btn sm primary" type="button" onclick="openNegotiationModal('${order.request_id}')">Negotiate</button>`;
        } else if (order.status === 'still_getting_info') {
            action = `<button class="btn sm" type="button" onclick="openDealDetails('${order.request_id}')">View</button>`;
        }

        return `
            <div class="list-row">
                <span class="dot ${order.status === 'negotiating' ? 'neg' : order.status === 'quote_sent' ? 'ok' : 'rev'}"></span>
                <div class="body">
                    <div class="ttl">${esc(STATUS_LABELS[order.status] || order.status.replace(/_/g, ' '))}</div>
                    <div class="meta">${esc(order.delivery_country || 'Unknown')} · ${esc((order.order_text || '').substring(0, 48))}${(order.order_text || '').length > 48 ? '…' : ''}</div>
                </div>
                <div class="right">${action}</div>
            </div>
        `;
    }).join('');
}

function renderOverview(counts) {
    const list = document.getElementById('overview-list');
    if (!list) return;

    const overview = [

        { label: 'Negotiating', value: counts.negotiating || 0, cls: 'neg' },
        { label: 'Quote sent', value: counts.quote_sent || 0, cls: 'ok' },
        { label: 'Needs info', value: counts.still_getting_info || 0, cls: 'rev' },
        { label: 'Canceled / error', value: (counts.canceled || 0) + (counts.error || 0), cls: 'rev' },
    ];

    list.innerHTML = overview.map(item => `
        <div class="list-row">
            <span class="dot ${item.cls === 'ok' ? 'ok' : item.cls === 'neg' ? 'neg' : 'rev'}"></span>
            <div class="body">
                <div class="ttl">${esc(item.label)}</div>
                <div class="meta">Current queue status</div>
            </div>
            <div class="right">${item.value}</div>
        </div>
    `).join('');
}

function handleDealsTabClick(event) {
        const button = event.target.closest('.tab');
        if (!button) return;
        document.querySelectorAll('.deal-tabs .tab').forEach(item => item.classList.remove('on'));
        button.classList.add('on');
        const tab = button.dataset.tab;
        document.querySelectorAll('#view-deals .section').forEach(section => {
                const category = section.dataset.cat;
                section.style.display = tab === 'all' || tab === category ? '' : 'none';
        });
}

function dealGroupForStatus(status) {
        if (status === 'negotiating') return 'neg';
    if (status === 'quote_sent') return 'yes';
    if (status === 'still_getting_info') return 'rev';
        return 'rev';
}

function dealLabelForStatus(status) {
        return STATUS_LABELS[status] || String(status || 'Review').replace(/_/g, ' ');
}

function dealAmountForOrder(order) {
        if (Array.isArray(order.proposals) && order.proposals.length) {
                return order.proposals.reduce((total, proposal) => {
                        const quantity = Number(proposal?.proposal?.offer_quantity || proposal?.requested_quantity || 0);
                        const price = Number(proposal?.proposal?.offer_unit_price || proposal?.wanted_unit_price || 0);
                        return total + (quantity * price);
                }, 0);
        }
        return 0;
}

function dealCountForOrder(order) {
        if (Array.isArray(order.proposals) && order.proposals.length) return order.proposals.length;
        return 1;
}

function renderDeals() {
        const yesItems = currentOrders.filter(order => dealGroupForStatus(order.status) === 'yes');
        const negItems = currentOrders.filter(order => dealGroupForStatus(order.status) === 'neg');
        const revItems = currentOrders.filter(order => dealGroupForStatus(order.status) === 'rev');

        setText('cAll', currentOrders.length);
        setText('cYes', yesItems.length);
        setText('cNeg', negItems.length);
        setText('cRev', revItems.length);
        setText('yesCount', `${yesItems.length} deals`);
        setText('negCount', `${negItems.length} deals`);
        setText('revCount', `${revItems.length} deals`);

        const renderHead = (order) => {
                const amount = dealAmountForOrder(order);
                const count = dealCountForOrder(order);
                return `
                        ${chBadge(order.status === 'quote_sent' ? 'pdf' : order.status === 'negotiating' ? 'email' : 'whats')}
                        <div class="client-cell">
                            <div class="ini" style="background:${iniColor(order.delivery_country || order.request_id)}">${esc(initials((order.delivery_country || order.request_id || 'OT').replace(/[^A-Za-z0-9 ]/g, ' ')))}</div>
                            <div>
                                <div class="nm">${esc(order.delivery_country || 'Unknown')}</div>
                                <div class="id">${esc(order.request_id)}</div>
                            </div>
                        </div>
                        <div class="info">
                            <span>${esc(dealLabelForStatus(order.status))}</span>
                            <span class="sep"></span>
                            <span>${count} items</span>
                            <span class="sep"></span>
                            <span>${esc(formatDate(order.created_at))}</span>
                        </div>
                        <div>${scoreCell(order.status === 'quote_sent' ? 90 : order.status === 'negotiating' ? 78 : 65)}</div>
                        <div class="amt">${amount > 0 ? `€${amount.toLocaleString()}` : '—'}</div>
                `;
        };

        const renderYesRow = (order) => `
                <div class="deal" data-id="${esc(order.request_id)}">
                    ${renderHead(order)}
                    <div class="deal-actions">
                        ${order.status === 'quote_sent'
                                ? `<span class="pill yes">Invoice sent</span><button class="btn sm" data-act="view" data-id="${esc(order.request_id)}">View</button>`
                                : `<span class="pill grey">Waiting for input</span><button class="btn sm" data-act="view" data-id="${esc(order.request_id)}">View</button>`}
                    </div>
                </div>`;

        const renderNegRow = (order) => {
                const proposals = Array.isArray(order.proposals) ? order.proposals : [];
                const first = proposals[0];
                const qty = first?.proposal?.offer_quantity ?? first?.requested_quantity ?? '';
                const price = first?.proposal?.offer_unit_price ?? first?.wanted_unit_price ?? '';
                const qtyFrom = first?.requested_quantity ?? '';
            const wantedPrice = first?.wanted_unit_price;
            const stockPrice = first?.stock_unit_price;
            const offerPrice = first?.proposal?.offer_unit_price;
            let priceFrom = '';

            if (stockPrice !== undefined && stockPrice !== null && offerPrice !== undefined && offerPrice !== null && Number(stockPrice) !== Number(offerPrice)) {
                priceFrom = Number(stockPrice).toFixed(2);
            } else if (wantedPrice !== undefined && wantedPrice !== null && offerPrice !== undefined && offerPrice !== null && Number(wantedPrice) !== Number(offerPrice)) {
                priceFrom = Number(wantedPrice).toFixed(2);
            }
                return `
                <div class="deal deal-neg" data-id="${esc(order.request_id)}">
                    ${renderHead(order)}
                    <div class="deal-actions">
                        <button class="btn sm danger" data-act="reject" data-id="${esc(order.request_id)}">Reject</button>
                        <button class="btn sm primary" data-act="counter" data-id="${esc(order.request_id)}">Send counter-offer</button>
                    </div>
                    <div class="neg-changes">
                        <div class="neg-cell">
                            <div class="l">Quantity</div>
                            <div class="v">
                                ${qtyFrom !== '' && qtyFrom !== qty ? `<span class="from">${qtyFrom}</span><span class="arrow">→</span>` : ''}
                                <input class="to" type="number" value="${esc(qty)}" />
                            </div>
                        </div>
                        <div class="neg-cell">
                            <div class="l">Unit price (€)</div>
                            <div class="v">
                                ${priceFrom !== '' ? `<span class="from">€${esc(priceFrom)}</span><span class="arrow">→</span>` : ''}
                                <input class="to" type="number" step="0.01" value="${esc(price)}" />
                            </div>
                        </div>
                    </div>
                </div>`;
        };

        const renderRevRow = (order) => {
                const label = 'Needs human review';
                const desc = order.review_reason || 'The request needs a manual check before quoting.';
                return `
                <div class="deal deal-rev" data-id="${esc(order.request_id)}">
                    ${renderHead(order)}
                    <div class="deal-actions">
                        <button class="btn sm" data-act="view" data-id="${esc(order.request_id)}">View</button>
                    </div>
                    <div class="rev-banner review">
                        <div class="icon">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/></svg>
                        </div>
                        <div>
                            <div class="ttl">${esc(label)}</div>
                            <div class="desc">${esc(desc)}</div>
                        </div>
                        <div class="actions">
                            <button class="btn sm" data-act="manual" data-id="${esc(order.request_id)}">Approve manually</button>
                            <button class="btn sm dark" data-act="human" data-id="${esc(order.request_id)}">Send to human</button>
                        </div>
                    </div>
                </div>`;
        };

        const yesList = document.getElementById('yesList');
        const negList = document.getElementById('negList');
        const revList = document.getElementById('revList');

        if (yesList) yesList.innerHTML = yesItems.map(renderYesRow).join('') || '<div class="empty-state">No accepted deals right now.</div>';
        if (negList) negList.innerHTML = negItems.map(renderNegRow).join('') || '<div class="empty-state">No negotiations right now.</div>';
        if (revList) revList.innerHTML = revItems.map(renderRevRow).join('') || '<div class="empty-state">No review items right now.</div>';

        document.querySelectorAll('#view-deals [data-act]').forEach(button => {
                button.onclick = () => {
                        const act = button.dataset.act;
                        const id = button.dataset.id;
                        if (act === 'counter') openNegotiationModal(id);
                        else if (act === 'reject') toast(`Negotiation rejected · ${id}`);
                        else if (act === 'manual') toast(`Approved manually · ${id}`);
                        else if (act === 'human') toast(`Reminder sent · ${id}`);
                        else if (act === 'send-quote') sendQuote(id, button);
                        else if (act === 'view') openDealDetails(id);
                };
        });
}

function buildDealDetailsContent(order) {
    const proposals = Array.isArray(order.proposals) && order.proposals.length ? order.proposals : [];
    const rows = proposals.length ? proposals : [{
        line_id: '-',
        product: 'No matched proposal',
        requested_quantity: '-',
        available_quantity: '-',
        wanted_unit_price: '-',
        proposal: { offer_quantity: '-', offer_unit_price: '-' },
    }];

    return `
        <div class="deal-detail-head">
            <div>
                <div class="detail-kicker">${esc(dealLabelForStatus(order.status))}</div>
                <h3>${esc(order.delivery_country || 'Unknown')} · ${esc(order.request_id)}</h3>
                <p>${esc(order.order_text || 'No order text available')}</p>
            </div>
            <div class="detail-meta">
                <div><span>Date</span><strong>${esc(formatDate(order.created_at))}</strong></div>
                <div><span>Status</span><strong>${esc(dealLabelForStatus(order.status))}</strong></div>
                <div><span>Country</span><strong>${esc(order.delivery_country || 'Unknown')}</strong></div>
            </div>
        </div>
        <div class="detail-lines">
            ${rows.map(item => `
                <div class="detail-line">
                    <div class="ttl">${esc(item.product || 'Product')}</div>
                    <div class="meta">Line ${esc(item.line_id)} · Requested ${esc(item.requested_quantity)} · Stock ${esc(item.available_quantity)} · Wanted €${esc(item.wanted_unit_price)}</div>
                    <div class="meta">Offer ${esc(item.proposal?.offer_quantity ?? '-')} @ €${esc(item.proposal?.offer_unit_price ?? '-')}</div>
                </div>
            `).join('')}
        </div>
    `;
}

function setModalMode(mode) {
    const saveButton = document.getElementById('btn-save-proposal');
    if (!saveButton) return;
    if (mode === 'view') {
        saveButton.style.display = 'none';
    } else {
        saveButton.style.display = '';
        saveButton.disabled = false;
        saveButton.textContent = 'Save & Send Quote';
    }
}

function openDealDetails(requestId) {
    const order = currentOrders.find(item => item.request_id === requestId);
    if (!order) return;

    currentRequestForModal = null;
    const title = document.querySelector('#negotiating-modal h2');
    const content = document.getElementById('modal-content');
    if (title) title.textContent = 'Deal details';
    if (content) content.innerHTML = buildDealDetailsContent(order);
    setModalMode('view');
    document.getElementById('negotiating-modal').classList.add('active');
}

function renderProductsIfVisible() {
    if (currentView === 'products') {
        renderProducts(currentProducts);
    }
}

function openNegotiationModal(requestId) {
    const order = currentOrders.find(item => item.request_id === requestId);
    if (!order || !Array.isArray(order.proposals) || !order.proposals.length) {
        return;
    }

    currentRequestForModal = requestId;
    const title = document.querySelector('#negotiating-modal h2');
    if (title) title.textContent = 'Negotiate Proposal';
    const content = document.getElementById('modal-content');
    content.innerHTML = '';

    order.proposals.forEach((proposal, index) => {
        const div = document.createElement('div');
        div.className = 'proposal-item';
        div.innerHTML = `
            <h4>${esc(proposal.product || 'Proposal')} (Line ${esc(proposal.line_id)})</h4>
            <div class="text-muted" style="margin-bottom: 12px;">
                Requested Qty: ${esc(proposal.requested_quantity)} (Stock: ${esc(proposal.available_quantity)})<br>
                Wanted Price: ${esc(proposal.wanted_unit_price)} (Stock Price: ${esc(proposal.stock_unit_price)})
            </div>
            <div class="input-group">
                <div class="input-wrapper">
                    <label>Offered Quantity</label>
                    <input type="number" id="offer-qty-${index}" value="${esc(proposal.proposal.offer_quantity)}">
                </div>
                <div class="input-wrapper">
                    <label>Offered Unit Price (€)</label>
                    <input type="number" step="0.01" id="offer-price-${index}" value="${esc(proposal.proposal.offer_unit_price)}">
                </div>
            </div>
        `;
        content.appendChild(div);
    });

    setModalMode('edit');
    document.getElementById('negotiating-modal').classList.add('active');
}

function closeModal() {
    document.getElementById('negotiating-modal').classList.remove('active');
    currentRequestForModal = null;
    const title = document.querySelector('#negotiating-modal h2');
    if (title) title.textContent = 'Negotiate Proposal';
    setModalMode('edit');
}

async function saveAndSendQuote() {
    if (!currentRequestForModal) return;

    const order = currentOrders.find(item => item.request_id === currentRequestForModal);
    if (!order) return;

    const updatedProposals = order.proposals.map((proposal, index) => ({
        line_id: proposal.line_id,
        proposal: {
            offer_quantity: parseFloat(document.getElementById(`offer-qty-${index}`).value),
            offer_unit_price: parseFloat(document.getElementById(`offer-price-${index}`).value),
        },
    }));

    try {
        await fetch(`/api/dashboard/orders/${currentRequestForModal}/update-lines`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proposals: updatedProposals }),
        });

        await sendQuote(currentRequestForModal);
        closeModal();
    } catch (error) {
        console.error('Error saving and sending quote:', error);
        alert('An error occurred. Check console.');
    }
}

async function sendQuote(requestId, button) {
    try {
        if (button) {
            button.disabled = true;
            button.textContent = 'Sending...';
        }

        const response = await fetch(`/api/dashboard/orders/${requestId}/send-quote`, { method: 'POST' });
        const data = await response.json();

        if (data.success) {
            await fetchOrders();
            await fetchClients();
        } else {
            alert('Failed to send quote');
        }
    } catch (error) {
        console.error('Error sending quote:', error);
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = 'Send quote';
        }
    }
}

function toast(message) {
    const existing = document.getElementById('toast');
    if (!existing) return;
    existing.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#65f0ad" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> ${esc(message)}`;
    existing.classList.add('show');
    clearTimeout(existing._h);
    existing._h = setTimeout(() => existing.classList.remove('show'), 2200);
}

async function showClientOrders(clientId) {
    try {
        const response = await fetch(`/api/dashboard/clients/${clientId}/orders`);
        const data = await response.json();
        const orders = data.orders || [];
        
        // Create a panel to display orders
        let ordersPanel = document.getElementById('client-orders-panel');
        if (!ordersPanel) {
            ordersPanel = document.createElement('div');
            ordersPanel.id = 'client-orders-panel';
            ordersPanel.style.cssText = `
                position: fixed;
                right: 0;
                top: 0;
                width: 400px;
                height: 100vh;
                background: white;
                border-left: 1px solid #e0e0e0;
                box-shadow: -2px 0 8px rgba(0,0,0,0.1);
                overflow-y: auto;
                z-index: 1000;
                padding: 20px;
                box-sizing: border-box;
            `;
            document.body.appendChild(ordersPanel);
        }
        
        // Build orders HTML
        const ordersHTML = orders.length ? orders.map(order => `
            <div style="margin-bottom: 16px; padding: 12px; border: 1px solid #f0f0f0; border-radius: 4px;">
                <div style="font-weight: 600; color: #333;">${esc(order.order_id)}</div>
                <div style="font-size: 12px; color: #999; margin-top: 4px;">${esc(order.order_date)}</div>
                <div style="margin-top: 8px; font-size: 13px;">
                    <div>${esc(order.product_name)}</div>
                    <div style="color: #666;">Qty: ${esc(order.quantity)} @ €${esc(order.unit_price)}</div>
                    <div style="color: #666;">Total: €${esc(order.total_price)}</div>
                    <div style="margin-top: 4px;"><span style="display: inline-block; background: #f0f0f0; padding: 2px 8px; border-radius: 3px; font-size: 11px;">${esc(order.status)}</span></div>
                </div>
            </div>
        `).join('') : '<div style="color: #999; text-align: center; padding: 40px 0;">No orders found</div>';
        
        ordersPanel.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid #e0e0e0; padding-bottom: 12px;">
                <h3 style="margin: 0; color: #333;">Orders</h3>
                <button onclick="closeClientOrdersPanel()" style="background: none; border: none; font-size: 20px; cursor: pointer; color: #999;">&times;</button>
            </div>
            <div>${ordersHTML}</div>
        `;
        
        // Adjust main content if needed
        const main = document.querySelector('.main');
        if (main) {
            main.style.marginRight = '400px';
        }
    } catch (error) {
        console.error('Error fetching client orders:', error);
        alert('Failed to load orders');
    }
}

function closeClientOrdersPanel() {
    const ordersPanel = document.getElementById('client-orders-panel');
    if (ordersPanel) {
        ordersPanel.remove();
    }
    const main = document.querySelector('.main');
    if (main) {
        main.style.marginRight = '';
    }
}

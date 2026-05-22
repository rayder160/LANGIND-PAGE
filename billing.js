document.addEventListener('DOMContentLoaded', async () => {
  try {
    const res = await fetch('/api/billing');
    if (!res.ok) throw new Error('Error al obtener datos de facturación');
    const data = await res.json();
    // Plan
    document.getElementById('plan-name').textContent = data.plan.name;
    document.getElementById('plan-status').textContent = data.plan.status;
    document.getElementById('plan-renewal').textContent = data.plan.renewal_date;
    document.getElementById('plan-price').textContent = data.plan.price_monthly;
    document.getElementById('plan-models').textContent = data.plan.models_allowed.join(', ');
    document.getElementById('plan-features').textContent = data.plan.features.join(', ');
    // Current month usage
    const cur = data.current_month;
    document.getElementById('usage-spend').textContent = cur.spend;
    document.getElementById('usage-limit').textContent = cur.limit;
    document.getElementById('usage-percent').textContent = cur.percent_used;
    document.getElementById('usage-messages').textContent = cur.messages_sent;
    document.getElementById('usage-tokens').textContent = cur.tokens_used;
    document.getElementById('usage-reco').textContent = cur.recommendation || 'N/A';
    // Alerts
    const alertsList = document.getElementById('alerts-list');
    data.alerts.forEach(a => {
      const li = document.createElement('li');
      li.textContent = `${a.type.toUpperCase()}: ${a.message}`;
      alertsList.appendChild(li);
    });
    // Payment history
    const tbody = document.getElementById('payment-body');
    data.payment_history.forEach(p => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${p.date}</td><td>$${p.amount}</td><td>${p.status}</td><td>${p.invoice}</td>`;
      tbody.appendChild(tr);
    });
    // Upcoming invoice
    document.getElementById('upcoming-date').textContent = data.upcoming_invoice.date;
    document.getElementById('upcoming-amount').textContent = data.upcoming_invoice.amount;
    document.getElementById('upcoming-status').textContent = data.upcoming_invoice.status;
  } catch (err) {
    console.error(err);
    const container = document.querySelector('main');
    const errorDiv = document.createElement('div');
    errorDiv.style.color = 'red';
    errorDiv.textContent = 'No se pudieron cargar los datos de facturación.';
    container.prepend(errorDiv);
  }
});

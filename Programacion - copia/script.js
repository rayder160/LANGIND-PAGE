document.addEventListener('DOMContentLoaded', function () {

  // ===== PARTICULAS DE FONDO =====
  const canvas = document.getElementById('particles-canvas');
  if (canvas) {
    const ctx = canvas.getContext('2d');
    let W = canvas.width = window.innerWidth;
    let H = canvas.height = window.innerHeight;
    const particles = [];
    for (let i = 0; i < 80; i++) {
      particles.push({
        x: Math.random() * W, y: Math.random() * H,
        r: Math.random() * 1.5 + 0.5,
        dx: (Math.random() - 0.5) * 0.4,
        dy: (Math.random() - 0.5) * 0.4,
        alpha: Math.random() * 0.5 + 0.1
      });
    }
    function drawParticles() {
      ctx.clearRect(0, 0, W, H);
      particles.forEach(p => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(0,180,255,' + p.alpha + ')';
        ctx.fill();
        p.x += p.dx; p.y += p.dy;
        if (p.x < 0 || p.x > W) p.dx *= -1;
        if (p.y < 0 || p.y > H) p.dy *= -1;
      });
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dist = Math.hypot(particles[i].x - particles[j].x, particles[i].y - particles[j].y);
          if (dist < 100) {
            ctx.beginPath();
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.strokeStyle = 'rgba(0,180,255,' + (0.08 * (1 - dist / 100)) + ')';
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }
      requestAnimationFrame(drawParticles);
    }
    drawParticles();
    window.addEventListener('resize', () => {
      W = canvas.width = window.innerWidth;
      H = canvas.height = window.innerHeight;
    });
  }

  // ===== CHAT HERO ANIMADO =====
  // Grupos de 4 mensajes (2 pares pregunta/respuesta)
  // Cada recarga elige un grupo distinto al azar
  const heroChat = document.getElementById('hero-chat');
  if (heroChat) {

    const groups = [
      [
        { type: 'user', role: 'Recursos Humanos', text: 'Como es el proceso de onboarding para nuevos empleados?' },
        { type: 'ai',   role: 'ProxDeep AI', text: 'Tiene 4 etapas: bienvenida el dia 1, capacitacion tecnica dias 2-5, asignacion de mentor semana 2 y evaluacion al mes.' },
        { type: 'user', role: 'Operaciones', text: 'Cual es el flujo para aprobar una solicitud de gastos?' },
        { type: 'ai',   role: 'ProxDeep AI', text: 'Tu lider valida el monto, Finanzas revisa comprobantes y Direccion aprueba si supera el limite del area.' },
      ],
      [
        { type: 'user', role: 'Dev - VS Code', text: 'Genera una funcion para validar el limite de gastos.' },
        { type: 'code', role: 'ProxDeep - Codigo', text: 'function validarGasto(monto, limite) {\n  return monto <= limite;\n}' },
        { type: 'user', role: 'Tecnologia', text: 'Explica la arquitectura del modulo de pagos.' },
        { type: 'ai',   role: 'ProxDeep AI', text: 'Usa una API REST con JWT. Los pagos pasan por un gateway externo y se registran con estado pendiente, aprobado o rechazado.' },
      ],
      [
        { type: 'user', role: 'Ventas', text: 'Como respondo a un cliente que pregunta por precios?' },
        { type: 'ai',   role: 'ProxDeep AI', text: 'Saluda por su nombre, presenta el plan segun su tamano de empresa y ofrece una demo gratuita de 30 minutos.' },
        { type: 'user', role: 'Soporte Interno', text: 'Cuantos dias de vacaciones corresponden al primer ano?' },
        { type: 'ai',   role: 'ProxDeep AI', text: 'Segun la politica de RRHH vigente, corresponden 12 dias habiles al cumplir el primer ano de antiguedad.' },
      ],
      [
        { type: 'user', role: 'Gerencia', text: 'Que areas tienen mas tickets de soporte este mes?' },
        { type: 'ai',   role: 'ProxDeep AI', text: 'Segun los registros: Operaciones 34%, Ventas 28%, TI 22% y RRHH 16%. Operaciones lidera por dudas de procesos internos.' },
        { type: 'user', role: 'Dev - VS Code', text: 'Refactoriza esta funcion para que sea mas eficiente.' },
        { type: 'code', role: 'ProxDeep - Codigo', text: 'const calcTotal = (items) =>\n  items.reduce((sum, i) => sum + i.precio, 0);' },
      ],
    ];

    // Elegir grupo aleatorio al cargar la pagina
    const groupIdx = Math.floor(Math.random() * groups.length);
    // Ciclo: al terminar el grupo actual, pasa al siguiente
    let currentGroup = groupIdx;
    let msgIdx = 0;

    const typingEl = document.createElement('div');
    typingEl.className = 'typing-indicator';
    typingEl.innerHTML = '<span></span><span></span><span></span>';
    heroChat.appendChild(typingEl);

    function clearChat(callback) {
      const msgs = heroChat.querySelectorAll('.msg');
      msgs.forEach(m => {
        m.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
        m.style.opacity = '0';
        m.style.transform = 'translateY(-8px)';
      });
      setTimeout(() => {
        msgs.forEach(m => m.remove());
        callback();
      }, 450);
    }

    function showNextMessage() {
      const messages = groups[currentGroup];

      if (msgIdx >= messages.length) {
        // Pausa antes de limpiar y pasar al siguiente grupo
        setTimeout(() => {
          clearChat(() => {
            currentGroup = (currentGroup + 1) % groups.length;
            msgIdx = 0;
            setTimeout(showNextMessage, 500);
          });
        }, 2500);
        return;
      }

      const m = messages[msgIdx];
      const isAI = m.type === 'ai' || m.type === 'code';

      if (isAI) {
        typingEl.classList.add('show');
        setTimeout(() => {
          typingEl.classList.remove('show');
          insertMessage(m);
          msgIdx++;
          setTimeout(showNextMessage, 1600);
        }, 1300);
      } else {
        insertMessage(m);
        msgIdx++;
        setTimeout(showNextMessage, 1000);
      }
    }

    function insertMessage(m) {
      const div = document.createElement('div');
      div.className = 'msg ' + (m.type === 'user' ? 'msg-user' : 'msg-ai') + (m.type === 'code' ? ' code' : '');
      const role = document.createElement('span');
      role.className = 'role';
      role.textContent = m.role;
      div.appendChild(role);
      if (m.type === 'code') {
        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.textContent = m.text;
        pre.appendChild(code);
        div.appendChild(pre);
      } else {
        const p = document.createElement('p');
        p.textContent = m.text;
        div.appendChild(p);
      }
      heroChat.insertBefore(div, typingEl);
      requestAnimationFrame(() => div.classList.add('show'));
    }

    setTimeout(showNextMessage, 800);
  }

  // ===== NAVEGACION MOBILE =====
  const navToggle = document.querySelector('.nav-toggle');
  const navLinks  = document.querySelector('.nav-links');
  const navCta    = document.querySelector('.nav-cta');
  if (navToggle) {
    navToggle.addEventListener('click', () => {
      navLinks && navLinks.classList.toggle('open');
      navCta   && navCta.classList.toggle('open');
    });
  }

  // ===== ANIMACIONES EN SCROLL =====
  const animated = document.querySelectorAll('[data-animate]');
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.15 });
  animated.forEach(el => observer.observe(el));

  // ===== METRICAS ANIMADAS =====
  const metricNumbers = document.querySelectorAll('.metric-number');
  const metricObserver = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const el = entry.target;
        const target = parseInt(el.dataset.target);
        let current = 0;
        const step = Math.ceil(target / 40);
        const timer = setInterval(() => {
          current = Math.min(current + step, target);
          el.textContent = current;
          if (current >= target) clearInterval(timer);
        }, 40);
        metricObserver.unobserve(el);
      }
    });
  }, { threshold: 0.5 });
  metricNumbers.forEach(el => metricObserver.observe(el));

  // ===== ROBOT ASISTENTE =====
  const botBtn      = document.getElementById('bot-btn');
  const botChat     = document.getElementById('bot-chat');
  const botClose    = document.getElementById('bot-close');
  const botInput    = document.getElementById('bot-input');
  const botSend     = document.getElementById('bot-send');
  const botMessages = document.getElementById('bot-chat-messages');
  const botBubble   = document.getElementById('bot-bubble-text');

  const bubbleTexts = [
    'IA disponible 24/7',
    'Aprende de tu empresa',
    'Integra con VS Code',
    'Datos 100% privados',
    'Respuestas en segundos',
    'Sin entrenar modelos publicos',
  ];
  let bubbleIdx = 0;
  setInterval(() => {
    if (!botBubble) return;
    botBubble.style.opacity = '0';
    setTimeout(() => {
      bubbleIdx = (bubbleIdx + 1) % bubbleTexts.length;
      botBubble.textContent = bubbleTexts[bubbleIdx];
      botBubble.style.opacity = '1';
    }, 300);
  }, 3000);

  if (botBtn)   botBtn.addEventListener('click',  () => botChat.classList.toggle('open'));
  if (botClose) botClose.addEventListener('click', () => botChat.classList.remove('open'));

  function sendBotMessage() {
    if (!botInput) return;
    const text = botInput.value.trim();
    if (!text) return;
    const userMsg = document.createElement('div');
    userMsg.className = 'bot-msg bot-msg-user';
    userMsg.innerHTML = '<p>' + text + '</p>';
    botMessages.appendChild(userMsg);
    botInput.value = '';
    botMessages.scrollTop = botMessages.scrollHeight;
    setTimeout(() => {
      const aiMsg = document.createElement('div');
      aiMsg.className = 'bot-msg bot-msg-ai';
      aiMsg.innerHTML = '<p>Gracias por tu mensaje. Un asesor de ProxDeep se pondra en contacto contigo pronto. Puedes agendar una demo desde el formulario de contacto.</p>';
      botMessages.appendChild(aiMsg);
      botMessages.scrollTop = botMessages.scrollHeight;
    }, 1000);
  }

  if (botSend)  botSend.addEventListener('click', sendBotMessage);
  if (botInput) botInput.addEventListener('keypress', e => { if (e.key === 'Enter') sendBotMessage(); });

});
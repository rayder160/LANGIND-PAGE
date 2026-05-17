document.addEventListener('DOMContentLoaded', function () {

  // ===== NAVBAR HIDE/SHOW ON SCROLL =====
  const navSticky = document.getElementById('nav-sticky');
  if (navSticky) {
    let lastScrollY = window.scrollY;
    let ticking = false;
    window.addEventListener('scroll', () => {
      if (!ticking) {
        requestAnimationFrame(() => {
          const currentY = window.scrollY;
          if (currentY > lastScrollY && currentY > 80) {
            navSticky.classList.add('hidden');
          } else {
            navSticky.classList.remove('hidden');
          }
          if (currentY > 20) {
            navSticky.classList.add('scrolled');
          } else {
            navSticky.classList.remove('scrolled');
          }
          lastScrollY = currentY;
          ticking = false;
        });
        ticking = true;
      }
    });

    // Mobile toggle for sticky nav
    const navToggleSticky = document.getElementById('nav-toggle-sticky');
    const navLinksSticky  = document.getElementById('nav-links-sticky');
    const navCtaSticky    = document.getElementById('nav-cta-sticky');
    if (navToggleSticky) {
      navToggleSticky.addEventListener('click', () => {
        navLinksSticky && navLinksSticky.classList.toggle('open');
        navCtaSticky   && navCtaSticky.classList.toggle('open');
      });
    }
  }

  // ===== PRICING BACKGROUND CANVAS (Antigravity style) =====
  const pricingCanvas = document.getElementById('pricing-canvas');
  if (pricingCanvas) {
    const pCtx = pricingCanvas.getContext('2d');
    const wrap = pricingCanvas.parentElement;
    let pW = pricingCanvas.width  = wrap.offsetWidth;
    let pH = pricingCanvas.height = wrap.offsetHeight;

    const orbs = [
      { x: pW * 0.15, y: pH * 0.3,  r: 220, color: 'rgba(0,100,255,0.18)',  dx: 0.18, dy: 0.12 },
      { x: pW * 0.85, y: pH * 0.6,  r: 180, color: 'rgba(99,102,241,0.15)', dx: -0.14, dy: 0.1 },
      { x: pW * 0.5,  y: pH * 0.15, r: 150, color: 'rgba(0,180,255,0.12)', dx: 0.1,  dy: 0.15 },
    ];

    // Grid lines
    const gridLines = [];
    const cols = 12, rows = 8;
    for (let c = 0; c <= cols; c++) gridLines.push({ type: 'v', pos: (pW / cols) * c });
    for (let r = 0; r <= rows; r++) gridLines.push({ type: 'h', pos: (pH / rows) * r });

    function drawPricing() {
      pCtx.clearRect(0, 0, pW, pH);

      // Grid
      pCtx.save();
      pCtx.strokeStyle = 'rgba(0,180,255,0.06)';
      pCtx.lineWidth = 1;
      gridLines.forEach(l => {
        pCtx.beginPath();
        if (l.type === 'v') { pCtx.moveTo(l.pos, 0); pCtx.lineTo(l.pos, pH); }
        else                { pCtx.moveTo(0, l.pos); pCtx.lineTo(pW, l.pos); }
        pCtx.stroke();
      });
      pCtx.restore();

      // Orbs
      orbs.forEach(o => {
        const grad = pCtx.createRadialGradient(o.x, o.y, 0, o.x, o.y, o.r);
        grad.addColorStop(0, o.color);
        grad.addColorStop(1, 'transparent');
        pCtx.beginPath();
        pCtx.arc(o.x, o.y, o.r, 0, Math.PI * 2);
        pCtx.fillStyle = grad;
        pCtx.fill();
        o.x += o.dx; o.y += o.dy;
        if (o.x < -o.r || o.x > pW + o.r) o.dx *= -1;
        if (o.y < -o.r || o.y > pH + o.r) o.dy *= -1;
      });

      requestAnimationFrame(drawPricing);
    }
    drawPricing();

    const ro = new ResizeObserver(() => {
      pW = pricingCanvas.width  = wrap.offsetWidth;
      pH = pricingCanvas.height = wrap.offsetHeight;
    });
    ro.observe(wrap);
  }

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
      requestAnimationFrame(() => {
        div.classList.add('show');
        // Scroll to show latest message
        heroChat.scrollTop = heroChat.scrollHeight;
      });
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

  // ===== ROBOT ASISTENTE INTELIGENTE =====
  const botBtn      = document.getElementById('bot-btn');
  const botChat     = document.getElementById('bot-chat');
  const botClose    = document.getElementById('bot-close');
  const botInput    = document.getElementById('bot-input');
  const botSend     = document.getElementById('bot-send');
  const botMessages = document.getElementById('bot-chat-messages');
  const botBubble   = document.getElementById('bot-bubble-text');

  // --- Base de conocimiento de ProxDeep ---
  const KB = [
    {
      keys: ['hola','buenas','hey','saludos','buenos dias','buenas tardes','buenas noches'],
      answers: [
        '¡Hola! Soy el asistente de ProxDeep. Puedo ayudarte con información sobre nuestros productos, planes, implementación y más. ¿Qué necesitas saber?',
        '¡Buenas! Estoy aquí para resolver tus dudas sobre ProxDeep. ¿En qué te puedo ayudar?'
      ]
    },
    {
      keys: ['que es proxdeep','que hace proxdeep','de que trata','para que sirve','que ofrecen','que es esto'],
      answers: [
        'ProxDeep es una plataforma de IA empresarial con dos productos: **IA Organizacional** (monitorea ventas, operaciones y conocimiento interno de tu empresa) y **IA para Código** (copiloto en VS Code que entiende tu arquitectura). Ambos corren en servidores privados, sin compartir tus datos.'
      ]
    },
    {
      keys: ['ia organizacional','ia para empresa','asistente empresa','conocimiento empresa','procesos','operaciones','ventas','rrhh','recursos humanos'],
      answers: [
        'La **IA Organizacional** aprende de tus documentos, wikis, correos y bases internas. Puede responder preguntas de RRHH, detectar errores en ventas, generar reportes por área y resolver el 60% de los tickets internos automáticamente. Cada área ve solo lo que le corresponde.'
      ]
    },
    {
      keys: ['vscode','vs code','visual studio','codigo','copiloto','programar','extension','desarrolladores','dev'],
      answers: [
        'La **IA para Código** es una extensión de VS Code que entiende tu arquitectura, tus convenciones y tus reglas de negocio. Sugiere código alineado con tus estándares, documenta funciones automáticamente y hace refactors que respetan lo que ya tienes construido. No es un Copilot genérico.'
      ]
    },
    {
      keys: ['servidor','servidores','dedicado','compartido','infraestructura','hosting','privacidad','datos','seguridad'],
      answers: [
        'Ofrecemos dos opciones de infraestructura:\n• **Servidor compartido** — alto rendimiento, costo optimizado, aislamiento lógico. Ideal para pymes.\n• **Servidor dedicado** — recursos exclusivos para tu empresa, aislamiento físico total.\nEn ambos casos, tus datos nunca se usan para entrenar modelos públicos.'
      ]
    },
    {
      keys: ['planes','precio','costo','cuanto cuesta','starter','business','enterprise','tarifa'],
      answers: [
        'Tenemos 3 planes:\n• **Starter** — 1 área, servidor compartido, hasta 15 usuarios.\n• **Business** — múltiples áreas, servidor compartido o dedicado, VS Code incluido.\n• **Enterprise** — servidor dedicado, integraciones personalizadas, SLA 24/7.\nPuedes ver la comparativa completa en la página de Planes o agendar una demo para recibir una propuesta personalizada.'
      ]
    },
    {
      keys: ['demo','prueba','ver en accion','mostrar','probar','agendar','reunion','llamada'],
      answers: [
        '¡Claro! La demo es gratuita, dura 30 minutos y la hacemos con datos similares a los de tu empresa. Sin presentaciones genéricas. Puedes agendarla en proxdeep.com/demo o haciendo clic en "Agendar demo" en el menú.'
      ]
    },
    {
      keys: ['implementacion','cuanto tarda','tiempo','semanas','meses','puesta en marcha','instalar','configurar'],
      answers: [
        'La implementación tiene 4 pasos:\n1. Diagnóstico rápido (30–60 min)\n2. Conexión a tus documentos y herramientas\n3. Entrenamiento y ajustes con feedback real\n4. Activación del copiloto en VS Code\nEl plan Starter puede estar operativo en 1–2 semanas. Business y Enterprise toman 2–4 semanas.'
      ]
    },
    {
      keys: ['integracion','integra','conecta','slack','notion','confluence','google drive','jira','salesforce','herramientas'],
      answers: [
        'ProxDeep se conecta con las herramientas que ya usas: Google Drive, Notion, Confluence, correo corporativo, Slack, Jira, Salesforce y más. También tiene API REST y webhooks para integraciones personalizadas.'
      ]
    },
    {
      keys: ['privado','mis datos','entrenar','modelo publico','confidencial','gdpr','seguro','cifrado'],
      answers: [
        'Tus datos son completamente privados. ProxDeep **nunca** usa tu información para entrenar modelos públicos. Cada instancia está aislada, los datos van cifrados con AES-256 y TLS 1.3, y puedes solicitar eliminación en cualquier momento.'
      ]
    },
    {
      keys: ['soporte','ayuda','contacto','hablar','asesor','ventas','equipo'],
      answers: [
        'Puedes contactar al equipo de ProxDeep a través del formulario de demo en proxdeep.com/demo. Un asesor te responde en menos de 24 horas hábiles. También puedes escribirnos directamente si tienes una pregunta urgente.'
      ]
    },
    {
      keys: ['diferencia','competencia','copilot','chatgpt','openai','github copilot','notion ai','vs'],
      answers: [
        'La diferencia clave es el **contexto privado**. Herramientas como GitHub Copilot o ChatGPT son genéricas y no conocen tu empresa. ProxDeep aprende de tus documentos internos, corre en servidores privados y sus respuestas están alineadas con tus procesos reales, no con información pública.'
      ]
    },
    {
      keys: ['gracias','perfecto','entendi','ok','listo','genial','excelente'],
      answers: [
        '¡Con gusto! Si tienes más preguntas, aquí estoy. También puedes agendar una demo para ver ProxDeep en acción con datos reales.',
        '¡Perfecto! Cualquier otra duda, no dudes en preguntar. Puedes agendar tu demo gratuita cuando quieras.'
      ]
    },
  ];

  // Normaliza texto: minúsculas, sin tildes, sin puntuación
  function normalize(str) {
    return str.toLowerCase()
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9\s]/g, ' ')
      .replace(/\s+/g, ' ').trim();
  }

  // Busca la mejor respuesta por coincidencia de palabras clave
  function getBotResponse(input) {
    const norm = normalize(input);
    let bestMatch = null;
    let bestScore = 0;

    for (const entry of KB) {
      let score = 0;
      for (const key of entry.keys) {
        if (norm.includes(normalize(key))) {
          // Frases largas pesan más que palabras sueltas
          score += key.split(' ').length;
        }
      }
      if (score > bestScore) {
        bestScore = score;
        bestMatch = entry;
      }
    }

    if (bestMatch && bestScore > 0) {
      const answers = bestMatch.answers;
      return answers[Math.floor(Math.random() * answers.length)];
    }

    // Fallback
    return 'No estoy seguro de cómo responder eso, pero el equipo de ProxDeep puede ayudarte. Puedes agendar una demo gratuita en proxdeep.com/demo o escribirnos directamente.';
  }

  // Renderiza markdown básico (**negrita**, saltos de línea, bullets)
  function renderMarkdown(text) {
    return text
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\n•/g, '<br>•')
      .replace(/\n(\d+\.)/g, '<br>$1')
      .replace(/\n/g, '<br>');
  }

  // --- Burbujas rotativas ---
  const bubbleTexts = [
    'IA disponible 24/7',
    'Aprende de tu empresa',
    'Integra con VS Code',
    'Datos 100% privados',
    'Respuestas en segundos',
    'Sin entrenar modelos públicos',
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

  function appendBotMsg(html, isUser) {
    const div = document.createElement('div');
    div.className = 'bot-msg ' + (isUser ? 'bot-msg-user' : 'bot-msg-ai');
    div.innerHTML = '<p>' + html + '</p>';
    botMessages.appendChild(div);
    botMessages.scrollTop = botMessages.scrollHeight;
    return div;
  }

  function showTyping() {
    const t = document.createElement('div');
    t.className = 'bot-msg bot-msg-ai bot-typing-bubble';
    t.innerHTML = '<p class="bot-typing-dots"><span></span><span></span><span></span></p>';
    botMessages.appendChild(t);
    botMessages.scrollTop = botMessages.scrollHeight;
    return t;
  }

  function sendBotMessage() {
    if (!botInput) return;
    const text = botInput.value.trim();
    if (!text) return;

    appendBotMsg(text.replace(/</g, '&lt;'), true);
    botInput.value = '';

    // Simula tiempo de "escritura" (600–1200ms)
    const delay = 600 + Math.random() * 600;
    const typingBubble = showTyping();

    setTimeout(() => {
      typingBubble.remove();
      const response = getBotResponse(text);
      appendBotMsg(renderMarkdown(response), false);
    }, delay);
  }

  if (botSend)  botSend.addEventListener('click', sendBotMessage);
  if (botInput) botInput.addEventListener('keypress', e => { if (e.key === 'Enter') sendBotMessage(); });

});
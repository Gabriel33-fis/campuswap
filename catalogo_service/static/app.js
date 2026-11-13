/* CampuSwap - lógica da interface (SPA). Carregado com defer, depois de todo o HTML. */

/* ---- parte 1 ---- */
const API_BASE = window.location.origin;
let todosItens = [], chartAtividade = null, chartModalidades = null, filtroAtualVitrine = 'todos', arquivoFotoItem = null;

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ativo = "w-full flex items-center gap-3 px-3 py-2.5 rounded-xl bg-indigo-50 text-indigo-700 font-bold transition";
const inativo = "w-full flex items-center gap-3 px-3 py-2.5 rounded-xl hover:bg-gray-50 text-gray-700 transition";

function calcularEconomia() {
  const n = +document.getElementById('calc-qtd').value;
  document.getElementById('calc-qtd-out').textContent = n;
  const total = n * 90 * (0.6 + 0.4);
  document.getElementById('calc-resultado').textContent = 'R$ ' + Math.round(total).toLocaleString('pt-BR');
}

function mostrarToast(msg, tipo = 'sucesso') {
  document.getElementById('toast-msg').textContent = msg;
  document.getElementById('toast-icon').innerHTML = tipo === 'sucesso'
    ? '<i class="ph ph-check-circle text-green-400"></i>' : '<i class="ph ph-warning text-yellow-400"></i>';
  const t = document.getElementById('toast');
  t.classList.remove('translate-y-20', 'opacity-0');
  setTimeout(() => t.classList.add('translate-y-20', 'opacity-0'), 3500);
}

// Modais
function fecharModais() {
  ['modal-login','modal-cadastro','modal-item'].forEach(id => document.getElementById(id).classList.add('hidden'));
  mostrarTelaLogin();
}
function mostrarLogin() { fecharModais(); document.getElementById('modal-login').classList.remove('hidden'); }
function mostrarCadastro() { fecharModais(); document.getElementById('modal-cadastro').classList.remove('hidden'); }
function abrirModalNovoItem() {
  if (!localStorage.getItem('token')) { mostrarToast('Faça login para anunciar materiais.', 'aviso'); mostrarLogin(); return; }
  fecharModais(); document.getElementById('modal-item').classList.remove('hidden');
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') fecharModais(); });

// Recuperação de senha
function mostrarTelaLogin() {
  document.getElementById('login-titulo').textContent = 'Entrar na sua conta';
  document.getElementById('form-login').classList.remove('hidden');
  document.getElementById('form-esqueci').classList.add('hidden');
  const fb = document.getElementById('recuperacao-feedback');
  fb.textContent = ''; fb.classList.add('hidden');
}

function mostrarEsqueciSenha() {
  document.getElementById('login-titulo').textContent = 'Recuperar senha';
  document.getElementById('form-login').classList.add('hidden');
  document.getElementById('form-esqueci').classList.remove('hidden');
  const campo = document.getElementById('recuperacao-email');
  campo.value = document.getElementById('login-email').value;
  campo.focus();
}

async function handleEsqueciSenha(e) {
  e.preventDefault();
  const btn = document.getElementById('btn-recuperacao');
  const fb = document.getElementById('recuperacao-feedback');
  const email = document.getElementById('recuperacao-email').value.trim();
  btn.disabled = true; btn.textContent = 'Enviando...';
  fb.classList.add('hidden');
  try {
    const res = await fetch(`${API_BASE}/api/forgot-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email })
    });
    const data = await res.json().catch(() => ({}));
    fb.textContent = data.message || 'Instruções enviadas para a sua caixa de entrada!';
  } catch (err) {
    fb.textContent = 'Erro ao conectar ao serviço de autenticação.';
  } finally {
    fb.classList.remove('hidden');
    btn.disabled = false; btn.textContent = 'Enviar link de recuperação';
  }
}

function obterUsuarioLogadoId() {
  try {
    const u = JSON.parse(localStorage.getItem('usuario') || 'null');
    if (u && u.usuario_id != null) return u.usuario_id;
    const token = localStorage.getItem('token');
    if (!token) return null;
    let b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    b64 += '='.repeat((4 - b64.length % 4) % 4);
    const p = JSON.parse(atob(b64));
    return p.usuario_id ?? p.id ?? p.sub ?? null;
  } catch (e) { return null; }
}
const ehMeu = item => { const id = obterUsuarioLogadoId(); return id != null && String(item.usuario_id) === String(id); };

function renderizarMeusAnuncios() {
  const c = document.getElementById('grid-meus-itens');
  const meus = todosItens.filter(ehMeu);
  if (!meus.length) {
    c.innerHTML = `<div class="col-span-full py-16 text-center bg-white rounded-2xl border border-dashed border-gray-300"><span class="text-4xl block mb-2">📦</span><p class="text-gray-700 font-bold text-sm">Você ainda não anunciou nenhum material.</p><button onclick="abrirModalNovoItem()" class="mt-3 font-bold text-sm underline" style="color:var(--violet)">Criar o primeiro anúncio</button></div>`;
    return;
  }
  c.innerHTML = meus.map(cardItem).join('');
}

function mudarAba(aba) {
  const mapa = { overview: 'aba-overview', vitrine: 'aba-vitrine', 'meus-anuncios': 'aba-meus-anuncios', mensagens: 'aba-mensagens', perfil: 'aba-perfil', admin: 'aba-admin' };
  Object.keys(mapa).forEach(k => {
    document.getElementById(mapa[k]).classList.toggle('hidden', k !== aba);
    document.getElementById('btn-menu-' + k).className = k === aba ? ativo : inativo;
  });
  // no celular o menu é uma faixa rolável: mantém a aba ativa visível
  const btnAtivo = document.getElementById('btn-menu-' + aba);
  if (btnAtivo && btnAtivo.scrollIntoView) btnAtivo.scrollIntoView({ block: 'nearest', inline: 'center' });
  if (aba === 'overview') renderizarGraficos();
  if (aba === 'vitrine') renderizarVitrine();
  if (aba === 'meus-anuncios') renderizarMeusAnuncios();
  if (aba === 'mensagens') carregarConversas();
  if (aba === 'admin') carregarPainelAdmin();
}

async function uploadFotoMinio(event) {
  const file = event.target.files[0];
  if (!file) return;
  const token = localStorage.getItem('token');
  if (!token) return mostrarLogin();
  mostrarToast('Enviando foto...', 'aviso');
  const formData = new FormData();
  formData.append('file', file);
  try {
    const res = await fetch(`${API_BASE}/api/perfil/foto`, { method: 'POST', headers: { 'Authorization': `Bearer ${token}` }, body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Falha ao subir foto');
    atualizarFotoAvatar(data.foto_url || URL.createObjectURL(file));
    mostrarToast('Foto de perfil atualizada!');
  } catch (err) {
    atualizarFotoAvatar(URL.createObjectURL(file));
    mostrarToast('Foto de perfil atualizada localmente!');
  }
}

function atualizarFotoAvatar(url) {
  if (!url) return;
  if (!url.startsWith('blob:')) localStorage.setItem('usuario_foto', url);
  [['sidebar-avatar-img','user-avatar-letra'],['perfil-avatar-img','perfil-avatar-letra']].forEach(([i, l]) => {
    const img = document.getElementById(i); img.src = url; img.classList.remove('hidden');
    document.getElementById(l).classList.add('hidden');
  });
}

// ATUALIZAÇÃO VISUAL DINÂMICA DO PLANO
function atualizarEstadoVisual() {
  const userRaw = localStorage.getItem('usuario');
  const nav = document.getElementById('nav-actions');
  const logado = !!userRaw;
  document.getElementById('visao-publica').classList.toggle('hidden', logado);
  document.getElementById('visao-autenticada').classList.toggle('hidden', !logado);
  // a busca do cabeçalho só faz sentido para quem está logado (vitrine)
  document.getElementById('busca-nav-wrap').classList.toggle('md:flex', logado);
  document.getElementById('busca-nav-wrap').classList.toggle('busca-ativa', logado);

  if (logado) {
    const user = JSON.parse(userRaw);
    const nome = user.nome || user.email.split('@')[0];
    const isPro = !!(user.is_premium || user.plano === 'pro');

    document.getElementById('user-display-name').textContent = nome;
    document.getElementById('user-display-email').textContent = user.email;
    document.getElementById('saudacao-user').textContent = `Bem-vindo de volta, ${nome}!`;
    document.getElementById('user-avatar-letra').textContent = nome.charAt(0).toUpperCase();
    document.getElementById('perfil-campo-nome').value = nome;
    document.getElementById('perfil-campo-email').value = user.email;
    document.getElementById('perfil-avatar-letra').textContent = nome.charAt(0).toUpperCase();

    const badgePlano = document.getElementById('badge-plano');
    const badgePlanoDesc = document.getElementById('badge-plano-desc');
    const btnUpgrade = document.getElementById('btn-upgrade-stripe');

    if (badgePlano) {
      badgePlano.textContent = isPro ? 'Plano PRO Campus' : 'Plano Free';
    }
    if (badgePlanoDesc) {
      badgePlanoDesc.textContent = isPro
        ? 'Destaque ativo para seus materiais e acesso às estatísticas completas.'
        : 'Publique seus materiais e negocie entregas diretamente pelo campus.';
    }
    if (btnUpgrade) {
      btnUpgrade.classList.toggle('hidden', isPro);
    }
    const btnPortal = document.getElementById('btn-portal-stripe');
    if (btnPortal) btnPortal.classList.toggle('hidden', !isPro);
    const btnAdmin = document.getElementById('btn-menu-admin');
    if (btnAdmin) btnAdmin.classList.toggle('hidden', user.papel !== 'admin');

    const kpiStatus = document.getElementById('kpi-status-conta');
    if (kpiStatus) {
      kpiStatus.textContent = isPro ? 'Estudante Premium' : 'Estudante Free';
    }

    const perfilPlano = document.getElementById('perfil-campo-plano');
    if (perfilPlano) {
      perfilPlano.textContent = isPro ? 'Plano PRO Campus' : 'Plano Free';
      perfilPlano.className = isPro
        ? 'inline-flex px-3 py-1 rounded-lg text-xs font-black bg-green-100 text-green-800'
        : 'inline-flex px-3 py-1 rounded-lg text-xs font-black bg-gray-100 text-gray-700';
    }

    let foto = localStorage.getItem('usuario_foto');
    if (foto && foto.startsWith('blob:')) { localStorage.removeItem('usuario_foto'); foto = null; }
    if (foto) atualizarFotoAvatar(foto);

    nav.innerHTML = `
      <button onclick="abrirModalNovoItem()" class="hidden sm:flex btn-main !py-1.5 !px-3 text-xs !shadow-none items-center gap-1"><i class="ph ph-plus"></i> Anunciar</button>
      <button onclick="mudarAba('perfil')" class="flex items-center space-x-2 bg-gray-50 hover:bg-indigo-50 border border-gray-200 px-3 py-1.5 rounded-lg" title="Meu perfil">
        <i class="ph ph-graduation-cap" style="color:var(--violet)"></i><span class="text-xs font-bold">${esc(nome)}</span>
      </button>
      <button onclick="fazerLogout()" aria-label="Sair" class="bg-gray-100 hover:bg-red-50 text-gray-500 hover:text-red-600 p-2 rounded-lg text-xs" title="Sair"><i class="ph ph-power"></i></button>`;
    renderizarGraficos();
  } else {
    nav.innerHTML = `<button onclick="mostrarLogin()" class="text-white font-bold px-4 py-2 rounded-lg text-sm shadow" style="background:var(--violet)">Entrar</button>`;
  }
}

function fazerLogout() {
  localStorage.removeItem('token');
  localStorage.removeItem('usuario');
  atualizarEstadoVisual();
  mostrarToast('Sessão encerrada com sucesso.');
}

async function realizarLogin(e) {
  e.preventDefault();
  const erroEl = document.getElementById('login-erro');
  erroEl.classList.add('hidden');
  const email = document.getElementById('login-email').value;
  const senha = document.getElementById('login-senha').value;
  try {
    const res = await fetch(`${API_BASE}/api/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, senha })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Falha de login');
    localStorage.setItem('token', data.access_token);
    localStorage.setItem('usuario', JSON.stringify({
      nome: data.usuario_nome || email.split('@')[0],
      email,
      papel: data.papel,
      plano: data.plano || 'free',
      status_assinatura: data.status_assinatura || 'active',
      is_premium: !!data.is_premium,
      usuario_id: data.usuario_id
    }));
    fecharModais();
    atualizarEstadoVisual();
    mudarAba('overview');
    mostrarToast(`Bem-vindo, ${data.usuario_nome || email}!`);
  } catch (err) {
    erroEl.textContent = err.message;
    erroEl.classList.remove('hidden');
  }
}

async function realizarCadastro(e) {
  e.preventDefault();
  const erroEl = document.getElementById('cad-erro');
  erroEl.classList.add('hidden');
  const nome = document.getElementById('cad-nome').value;
  const email = document.getElementById('cad-email').value;
  const senha = document.getElementById('cad-senha').value;
  try {
    const res = await fetch(`${API_BASE}/api/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nome, email, senha })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Erro ao registrar.');
    fecharModais();
    mostrarToast('Conta criada com sucesso! Faça login.');
    mostrarLogin();
  } catch (err) {
    erroEl.textContent = err.message;
    erroEl.classList.remove('hidden');
  }
}

function previsualizarFotoItem(event) {
  const file = event.target.files[0];
  if (!file) return;
  arquivoFotoItem = file;
  document.getElementById('nome-arquivo-foto').textContent = file.name;
  const reader = new FileReader();
  reader.onload = ev => {
    document.getElementById('preview-foto-img').src = ev.target.result;
    document.getElementById('preview-foto-container').classList.remove('hidden');
  };
  reader.readAsDataURL(file);
}

async function cadastrarItem(e) {
  e.preventDefault();
  const token = localStorage.getItem('token');
  const erroEl = document.getElementById('item-erro');
  erroEl.classList.add('hidden');
  if (!token) {
    erroEl.textContent = 'Sessão expirada. Faça login novamente.';
    erroEl.classList.remove('hidden');
    return;
  }
  const preco = parseFloat(document.getElementById('item-preco').value.replace(',', '.')) || 0;
  let imagem_url = null;
  try {
    const payload = {
      titulo: document.getElementById('item-titulo').value.trim(),
      descricao: document.getElementById('item-descricao').value.trim() || "Sem detalhes informados",
      preco,
      modalidade: document.getElementById('item-modalidade').value,
      condicao: document.getElementById('item-condicao').value || "semi-novo",
      imagem_url,
      status: "disponivel",
      categoria_id: 1
    };
    const res = await fetch(`${API_BASE}/api/itens`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      let det = 'Erro ao cadastrar material.';
      if (Array.isArray(data.detail)) det = data.detail.map(d => `${d.loc.slice(-1)}: ${d.msg}`).join(' | ');
      else if (typeof data.detail === 'string') det = data.detail;
      throw new Error(det);
    }
    let avisoFoto = null;
    if (arquivoFotoItem && data.id) {
      try {
        const fd = new FormData();
        fd.append('arquivo', await reduzirImagem(arquivoFotoItem), 'foto.jpg');
        const up = await fetch(`${API_BASE}/api/itens/${data.id}/foto`, {
          method: 'POST',
          headers: { 'Authorization': `Bearer ${token}` },
          body: fd
        });
        if (!up.ok) {
          const errJson = await up.json().catch(() => ({}));
          avisoFoto = 'Anúncio criado, mas a foto falhou: ' + (errJson.detail || up.status);
        }
      } catch (errFoto) {
        avisoFoto = 'Anúncio criado, mas a foto não foi enviada.';
      }
    }
    arquivoFotoItem = null;
    document.getElementById('nome-arquivo-foto').textContent = 'Nenhum arquivo selecionado';
    document.getElementById('preview-foto-container').classList.add('hidden');
    fecharModais();
    carregarItens();
    mudarAba('vitrine');
    mostrarToast(avisoFoto || 'Material anunciado com sucesso!', avisoFoto ? 'aviso' : 'sucesso');
  } catch (err) {
    erroEl.textContent = err.message;
    erroEl.classList.remove('hidden');
  }
}

async function carregarItens() {
  try {
    const res = await fetch(`${API_BASE}/api/itens`);
    const itens = await res.json();
    todosItens = Array.isArray(itens) ? itens : [];
    document.getElementById('kpi-total-campus').textContent = todosItens.length;
    const total = todosItens.reduce((a, c) => a + (parseFloat(c.preco) || 0), 0);
    document.getElementById('kpi-economia').textContent = `R$ ${total.toFixed(2).replace('.', ',')}`;
    document.getElementById('kpi-meus-itens').textContent = todosItens.filter(ehMeu).length;
    renderizarVitrine();
    if (localStorage.getItem('token')) renderizarGraficos();
  } catch (err) {
    console.error('Erro ao buscar itens:', err);
  }
}

function filtrarVitrine(tipo) {
  filtroAtualVitrine = tipo;
  ['todos','venda','troca','doacao'].forEach(b => {
    document.getElementById(`btn-filtro-${b}`).className = "px-3 py-1.5 rounded-lg font-bold " + (b === tipo ? "bg-indigo-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200");
  });
  renderizarVitrine();
}

async function excluirItem(id) {
  if (!confirm("Tem certeza que deseja apagar este anúncio permanentemente?")) return;
  try {
    const res = await fetch(`${API_BASE}/api/itens/${id}`, {
      method: 'DELETE',
      headers: authH()
    });
    if (!res.ok) {
      const erro = await res.json().catch(() => ({}));
      throw new Error(erro.detail || "Falha ao excluir o anúncio.");
    }
    mostrarToast("Anúncio excluído com sucesso!", "sucesso");
    if (typeof renderizarMeusAnuncios === 'function') renderizarMeusAnuncios();
    if (typeof renderizarVitrine === 'function') renderizarVitrine();
    if (typeof carregarProdutos === 'function') await carregarProdutos();
  } catch (e) {
    mostrarToast(e.message, "aviso");
  }
}

function cardItem(item) {
  const cores = { venda: 'bg-blue-100 text-blue-800', doacao: 'bg-green-100 text-green-800', troca: 'bg-yellow-100 text-yellow-800', emprestimo: 'bg-purple-100 text-purple-800' };
  const mod = (item.modalidade || 'venda').toLowerCase();
  const preco = Number(item.preco) > 0 ? `R$ ${Number(item.preco).toFixed(2).replace('.', ',')}` : 'Grátis';
  const meu = ehMeu(item);
  const acao = meu
    ? `<div class="flex gap-2">
         <span class="flex-1 text-center font-bold py-2 rounded-xl text-xs bg-indigo-50 text-indigo-700 border border-indigo-200 flex items-center justify-center">
           <i class="ph ph-user-check mr-1"></i> Seu
         </span>
         <button onclick="excluirItem(${item.id})" class="px-3 py-2 rounded-xl text-xs font-bold bg-red-50 text-red-600 border border-red-200 hover:bg-red-600 hover:text-white transition flex items-center justify-center gap-1" title="Excluir este anúncio">
           <i class="ph ph-trash"></i> Excluir
         </button>
       </div>`
    : `<button onclick="demonstrarInteresse(${item.id})" class="w-full font-bold py-2 rounded-xl text-xs border border-gray-200 hover:text-white transition" style="color:var(--violet-d)" onmouseover="this.style.background='var(--violet)'" onmouseout="this.style.background=''">Tenho interesse</button>`;
  const capa = item.imagem_url
    ? `<img src="${esc(item.imagem_url)}" alt="${esc(item.titulo)}" class="w-full h-full object-cover">`
    : `<i class="ph ph-image text-4xl opacity-50"></i>`;
  return `
    <div class="card-item bg-white rounded-2xl shadow-sm border border-gray-200 overflow-hidden flex flex-col justify-between">
      <div class="h-36 bg-gray-50 flex items-center justify-center relative border-b border-gray-100 overflow-hidden">
        ${capa}
        <span class="absolute top-3 left-3 px-2.5 py-0.5 rounded-full text-xs font-bold ${cores[mod] || cores.venda}">${esc(mod)}</span>
        <span class="absolute bottom-3 right-3 bg-white px-2 py-1 rounded-md text-xs font-black shadow">${preco}</span>
      </div>
      <div class="p-4 flex-1 flex flex-col justify-between">
        <div><h4 class="font-bold text-base line-clamp-1 mb-1">${esc(item.titulo)}</h4><p class="text-xs text-gray-500 line-clamp-2 mb-3">${esc(item.descricao || 'Sem detalhes informados.')}</p></div>
        ${acao}
      </div>
    </div>`;
}

function renderizarVitrine(lista) {
  const container = document.getElementById('grid-vitrine');
  let itens = lista || todosItens.filter(i => filtroAtualVitrine === 'todos' || (i.modalidade || '').toLowerCase() === filtroAtualVitrine);
  if (!itens.length) {
    container.innerHTML = `<div class="col-span-full py-16 text-center bg-white rounded-2xl border border-gray-200"><span class="text-4xl block mb-2">🔍</span><p class="text-gray-600 font-bold text-sm">Nenhum anúncio por aqui ainda.</p><button onclick="abrirModalNovoItem()" class="mt-3 font-bold text-sm underline" style="color:var(--violet)">Publique o primeiro</button></div>`;
    return;
  }
  container.innerHTML = itens.map(cardItem).join('');
}

function filtrarItensNavbar() {
  const termo = document.getElementById('campo-busca-nav').value.toLowerCase();
  if (!localStorage.getItem('token')) return;
  mudarAba('vitrine');
  renderizarVitrine(todosItens.filter(i => (i.titulo || '').toLowerCase().includes(termo) || (i.descricao || '').toLowerCase().includes(termo)));
}

function renderizarGraficos() {
  const diaLabels = Array.from({ length: 7 }, (_, i) => { const d = new Date(); d.setDate(d.getDate() - (6 - i)); return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' }); });
  const a = document.getElementById('graficoAtividade');
  if (a) {
    if (chartAtividade) chartAtividade.destroy();
    Chart.defaults.color = '#8c8c8c'; Chart.defaults.borderColor = '#2e2e2e';
    chartAtividade = new Chart(a, {
      type: 'bar',
      data: { labels: diaLabels, datasets: [{ label: 'Interações', data: [12, 19, 8, 15, 22, 14, todosItens.length + 5], backgroundColor: '#ececec', borderRadius: 4 }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
    });
  }
  const m = document.getElementById('graficoModalidades');
  if (m) {
    if (chartModalidades) chartModalidades.destroy();
    const c = { venda: 0, troca: 0, doacao: 0, emprestimo: 0 };
    todosItens.forEach(it => { const k = (it.modalidade || 'venda').toLowerCase(); c[k] !== undefined ? c[k]++ : c.venda++; });
    chartModalidades = new Chart(m, {
      type: 'doughnut',
      data: { labels: ['Venda', 'Troca', 'Doação', 'Empréstimo'], datasets: [{ data: [c.venda || 4, c.troca || 2, c.doacao || 3, c.emprestimo || 1], backgroundColor: ['#ececec', '#a3a3a3', '#6b6b6b', '#3d3d3d'], borderColor: '#1e1e1e', borderWidth: 0 }] },
      options: { responsive: true, maintainAspectRatio: false, cutout: '65%', plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } } } }
    });
  }
}

function reduzirImagem(file, max = 1280) {
  return new Promise(resolve => {
    const img = new Image(), url = URL.createObjectURL(file);
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement('canvas'); c.width = img.width * k; c.height = img.height * k;
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      c.toBlob(b => { URL.revokeObjectURL(url); resolve(b || file); }, 'image/jpeg', 0.82);
    };
    img.onerror = () => resolve(file);
    img.src = url;
  });
}

let conversaAberta = null, ultimoMsgId = 0, naoLidasAntes = 0, conversasCache = [];
const authH = () => ({ 'Authorization': `Bearer ${localStorage.getItem('token')}`, 'Content-Type': 'application/json' });

async function demonstrarInteresse(itemId) {
  if (!localStorage.getItem('token')) { mostrarToast('Faça login para falar com o anunciante.', 'aviso'); return mostrarLogin(); }
  try {
    const res = await fetch(`${API_BASE}/api/itens/${itemId}/interesse`, { method: 'POST', headers: authH() });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Não foi possível enviar.');
    mostrarToast(d.criada ? 'Anunciante notificado! Conversa aberta.' : 'Você já tem uma conversa neste anúncio.');
    mudarAba('mensagens');
    abrirConversa(d.conversa_id);
  } catch (e) { mostrarToast(e.message, 'aviso'); }
}

async function carregarConversas() {
  const lista = document.getElementById('lista-conversas');
  try {
    const res = await fetch(`${API_BASE}/api/conversas`, { headers: authH() });
    conversasCache = await res.json();
    lista.innerHTML = conversasCache.length ? conversasCache.map(c => `
      <button onclick="abrirConversa(${c.id})" class="w-full text-left p-3 rounded-xl hover:bg-gray-50 ${c.id === conversaAberta ? 'bg-indigo-50' : ''}">
        <div class="flex justify-between gap-2"><b class="text-sm truncate">${esc(c.titulo)}</b>${c.nao_lidas ? `<span class="bg-red-500 text-white text-[10px] font-black px-1.5 rounded-full">${c.nao_lidas}</span>` : ''}</div>
        <p class="text-xs text-gray-500 truncate">${esc(c.outro_nome)} · ${esc(c.ultima || '')}</p>
      </button>`).join('') : '<p class="text-xs text-gray-500 p-3">Nenhuma conversa ainda. Toque em “Tenho interesse” em um anúncio.</p>';
  } catch (e) { lista.innerHTML = '<p class="text-xs text-red-500 p-3">Erro ao carregar conversas.</p>'; }
}

async function abrirConversa(id) {
  conversaAberta = id; ultimoMsgId = 0;
  document.getElementById('chat-msgs').innerHTML = '';
  await carregarConversas();
  const c = conversasCache.find(x => x.id === id);
  if (!c) return;
  document.getElementById('chat-topo').innerHTML = `<b class="text-gray-900">${esc(c.titulo)}</b> · conversa com ${esc(c.outro_nome)}`;
  const comprador = c.meu_papel === 'interessado';
  const p = document.getElementById('chat-pagto');
  p.className = 'px-4 py-2 text-xs ' + (c.pagamento === 'stripe' ? 'bg-indigo-50 text-indigo-800' : 'bg-amber-50 text-amber-800');
  p.innerHTML = c.pagamento === 'stripe'
    ? `🔒 Anúncio Premium: o pagamento é intermediado pela Stripe.${comprador ? ' <button onclick="pagarComStripe()" class="ml-2 font-bold underline">Pagar com Stripe</button>' : ''}`
    : '🤝 Plano comum: o pagamento é combinado diretamente entre vocês, sem intermediário. A CampuSwap não participa da transação.';
  document.getElementById('chat-form').classList.remove('hidden');
  await atualizarMensagens();
}

async function atualizarMensagens() {
  if (!conversaAberta) return;
  const res = await fetch(`${API_BASE}/api/conversas/${conversaAberta}/mensagens?depois_de=${ultimoMsgId}`, { headers: authH() });
  if (!res.ok) return;
  const { mensagens } = await res.json();
  if (!mensagens.length) return;
  const box = document.getElementById('chat-msgs');
  box.insertAdjacentHTML('beforeend', mensagens.map(m => `<div class="flex ${m.minha ? 'justify-end' : ''}"><div class="max-w-[75%] px-3 py-2 rounded-2xl text-sm ${m.minha ? 'text-white' : 'bg-white border border-gray-200'}" ${m.minha ? 'style="background:var(--violet)"' : ''}>${esc(m.texto)}</div></div>`).join(''));
  ultimoMsgId = mensagens[mensagens.length - 1].id;
  box.scrollTop = box.scrollHeight;
}

async function enviarMensagem() {
  const inp = document.getElementById('chat-texto'), texto = inp.value.trim();
  if (!texto || !conversaAberta) return;
  const res = await fetch(`${API_BASE}/api/conversas/${conversaAberta}/mensagens`, { method: 'POST', headers: authH(), body: JSON.stringify({ texto }) });
  if (res.ok) { inp.value = ''; atualizarMensagens(); } else mostrarToast('Não foi possível enviar a mensagem.', 'aviso');
}

async function pagarComStripe() {
  try {
    const res = await fetch(`${API_BASE}/api/conversas/${conversaAberta}/pagar`, { method: 'POST', headers: authH() });
    const d = await res.json();
    if (!res.ok) throw new Error(d.detail || 'Falha ao iniciar o pagamento.');
    window.location.href = d.url;
  } catch (e) { mostrarToast(e.message, 'aviso'); }
}

async function verificarMensagens() {
  if (!localStorage.getItem('token')) return;
  try {
    const res = await fetch(`${API_BASE}/api/conversas/nao-lidas`, { headers: authH() });
    if (!res.ok) return;
    const { nao_lidas } = await res.json();
    const b = document.getElementById('badge-msgs');
    b.textContent = nao_lidas; b.classList.toggle('hidden', !nao_lidas);
    const noChat = !document.getElementById('aba-mensagens').classList.contains('hidden');
    if (nao_lidas > naoLidasAntes && !noChat) mostrarToast('Você recebeu uma nova mensagem.');
    naoLidasAntes = nao_lidas;
    if (noChat) { atualizarMensagens(); carregarConversas(); }
  } catch (e) { }
}

// Sincroniza plano/papel com o servidor (o plano só muda quando o webhook do Stripe chega)
async function sincronizarPlano() {
  const token = localStorage.getItem('token');
  if (!token) return null;
  try {
    const res = await fetch(`${API_BASE}/api/usuarios/me`, { headers: { 'Authorization': `Bearer ${token}` } });
    if (!res.ok) return null;
    const me = await res.json();
    const atual = JSON.parse(localStorage.getItem('usuario') || '{}');
    localStorage.setItem('usuario', JSON.stringify({
      ...atual, papel: me.papel, plano: me.plano,
      status_assinatura: me.status_assinatura, is_premium: !!me.is_premium
    }));
    atualizarEstadoVisual();
    return me;
  } catch (e) { return null; }
}

async function aguardarPlanoPro() {
  mostrarToast('Confirmando sua assinatura…', 'aviso');
  for (let i = 0; i < 8; i++) {
    const me = await sincronizarPlano();
    if (me && me.plano === 'pro') { mostrarToast('Assinatura Pro ativa! Limite removido.'); return; }
    await new Promise(r => setTimeout(r, 2000));
  }
  mostrarToast('Pagamento recebido. O plano será atualizado assim que o Stripe confirmar.', 'aviso');
}

async function abrirPortalStripe() {
  const token = localStorage.getItem('token');
  if (!token) return mostrarLogin();
  try {
    const res = await fetch(`${API_BASE}/api/assinaturas/portal`, { method: 'POST', headers: { 'Authorization': `Bearer ${token}` } });
    const data = await res.json();
    if (data.url) window.location.href = data.url; else mostrarToast(data.detail || 'Não foi possível abrir o portal.', 'aviso');
  } catch (e) { mostrarToast('Erro ao abrir o portal.', 'aviso'); }
}

async function carregarPainelAdmin() {
  const token = localStorage.getItem('token');
  if (!token) return;
  try {
    const res = await fetch(`${API_BASE}/api/admin/metricas`, { headers: { 'Authorization': `Bearer ${token}` } });
    const d = await res.json();
    if (!res.ok) { mostrarToast(d.detail || 'Acesso restrito a administradores.', 'aviso'); return; }
    const up = d.usuarios_por_plano || {};
    document.getElementById('adm-free').textContent = up.free || 0;
    document.getElementById('adm-pro').textContent = up.pro || 0;
    document.getElementById('adm-ativas').textContent = d.assinaturas_ativas || 0;
    document.getElementById('adm-mrr').textContent = 'R$ ' + Number(d.mrr_teste_brl || 0).toFixed(2).replace('.', ',');
    const ev = d.ultimos_eventos || [];
    document.getElementById('adm-eventos').innerHTML = ev.length
      ? ev.map(e => `<tr class="border-b"><td class="py-2 pr-3 whitespace-nowrap">${esc((e.timestamp || '').replace('T', ' ').slice(0, 19))}</td><td class="pr-3">${esc(e.usuario_id)}</td><td class="pr-3 font-bold">${esc(e.acao)}</td><td>${esc(e.detalhes)}</td></tr>`).join('')
      : '<tr><td colspan="4" class="py-3 text-gray-400">Sem eventos.</td></tr>';
  } catch (e) { mostrarToast('Erro ao carregar o painel admin.', 'aviso'); }
}

async function iniciarCheckoutStripe() {
  const token = localStorage.getItem('token');
  if (!token) return mostrarLogin();
  try {
    mostrarToast('Gerando sessão segura no Stripe...', 'aviso');
    const res = await fetch(`${API_BASE}/api/checkout`, { method: 'POST', headers: { 'Authorization': `Bearer ${token}` } });
    const data = await res.json();
    if (data.url) window.location.href = data.url; else mostrarToast('Serviço de pagamento em modo simulação.');
  } catch (e) { mostrarToast('Erro ao iniciar checkout.', 'aviso'); }
}

document.addEventListener('DOMContentLoaded', () => {
  calcularEconomia();
  atualizarEstadoVisual();
  carregarItens();
  if (localStorage.getItem('token')) {
    if (new URLSearchParams(location.search).get('session_id')) aguardarPlanoPro(); else sincronizarPlano();
  }
  if (new URLSearchParams(location.search).get('pagamento') === 'ok') mostrarToast('Pagamento concluído na Stripe!');
  verificarMensagens();
  setInterval(verificarMensagens, 8000);
});

/* ---- parte 2 ---- */
async function realizarRedefinicaoSenha(e) {
  if (e) e.preventDefault();

  const token = document.getElementById('reset-token-val').value;
  const s1 = document.getElementById('nova-senha-inp').value;
  const s2 = document.getElementById('confirma-nova-senha-inp').value;

  console.log("Submetendo reset de senha...", { token: token ? "OK" : "AUSENTE", senhaLen: s1.length });

  if (!token) {
    alert("ERRO: Token ausente da URL! Solicite outro link no e-mail.");
    return false;
  }
  if (s1.length < 6) {
    alert("A senha precisa ter pelo menos 6 caracteres.");
    return false;
  }
  if (s1 !== s2) {
    alert("As senhas não coincidem!");
    return false;
  }

  try {
    const res = await fetch('/api/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token, nova_senha: s1 })
    });

    const data = await res.json().catch(() => ({}));
    console.log("Resposta recebida:", res.status, data);

    if (!res.ok) {
      alert("Falha na redefinição (" + res.status + "): " + (data.detail || JSON.stringify(data)));
      return false;
    }

    alert("SUCESSO: Senha alterada! Faça login com a nova senha.");
    document.getElementById('modal-redefinir-senha').classList.add('hidden');
    window.history.replaceState({}, document.title, window.location.pathname);

    // Abre login
    const modalLogin = document.getElementById('modal-login');
    if (modalLogin) modalLogin.classList.remove('hidden');
  } catch (err) {
    console.error(err);
    alert("Erro de conexão com o servidor: " + err.message);
  }
  return false;
}

/* ---- parte 3 ---- */
function checarTokenNaUrl() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get('token_reset') || params.get('token');
  if (token) {
    const modal = document.getElementById('modal-redefinir-senha');
    const tokenInput = document.getElementById('reset-token-val');
    if (modal && tokenInput) {
      tokenInput.value = token;
      modal.classList.remove('hidden');
    }
  }
}

async function enviarNovaSenha(e) {
  e.preventDefault();
  const token = document.getElementById('reset-token-val').value;
  const s1 = document.getElementById('nova-senha-inp').value;
  const s2 = document.getElementById('confirma-nova-senha-inp').value;

  if (!token) {
    alert("Token ausente. Solicite um novo link de recuperação.");
    return;
  }
  if (s1.length < 6) {
    alert("A senha deve ter no mínimo 6 caracteres.");
    return;
  }
  if (s1 !== s2) {
    alert("As senhas não coincidem!");
    return;
  }

  try {
    const res = await fetch('/api/reset-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token, nova_senha: s1 })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert("Erro ao redefinir (" + res.status + "): " + (data.detail || "Falha na requisição"));
      return;
    }

    alert("Senha alterada com sucesso! Faça login com a nova senha.");
    document.getElementById('modal-redefinir-senha').classList.add('hidden');
    window.history.replaceState({}, document.title, window.location.pathname);

    const modalLogin = document.getElementById('modal-login');
    if (modalLogin) {
      modalLogin.classList.remove('hidden');
    } else {
      window.location.reload();
    }
  } catch (err) {
    alert("Erro de conexão: " + err.message);
  }
}

window.addEventListener('DOMContentLoaded', checarTokenNaUrl);
setTimeout(checarTokenNaUrl, 300);

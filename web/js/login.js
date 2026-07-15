const API = window.location.origin;
if (localStorage.getItem('pi_auth') === '1') { window.location.href = 'home.html'; }

function togglePw() {
  const inp = document.getElementById('password');
  const ico = document.getElementById('pwIcon');
  const show = inp.type === 'password';
  inp.type = show ? 'text' : 'password';
  ico.className = show ? 'fa-regular fa-eye-slash' : 'fa-regular fa-eye';
}

function showErr(msg) {
  const box = document.getElementById('errBox');
  document.getElementById('errMsg').textContent = msg;
  box.classList.remove('show');
  void box.offsetWidth;
  box.classList.add('show');
}

async function doLogin(e) {
  e.preventDefault();
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  if (!username || !password) return;
  document.getElementById('errBox').classList.remove('show');
  const btn = document.getElementById('loginBtn');
  btn.disabled = true; btn.classList.add('loading');
  try {
    const res = await fetch(`${API}/api/login`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({username, password})
    });
    if (res.ok) {
      localStorage.setItem('pi_user', username);
      localStorage.setItem('pi_auth', '1');
      window.location.href = 'home.html';
    } else if (res.status === 401) {
      showErr('Invalid username or password. Please try again.');
    } else {
      showErr('Server error. Please try again shortly.');
    }
  } catch { showErr('Cannot reach server. Check your connection.'); }
  finally { btn.disabled = false; btn.classList.remove('loading'); }
}

// Moonraker login: when Moonraker requires a login, nginx checks every
// snapshot and touch request against it. Reuse the token that Mainsail or
// Fluidd saved in this browser (both serve from the same origin).
function authToken() {
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith('user-token-')) {
        const token = localStorage.getItem(key);
        if (token) return token;
      }
    }
  } catch (e) { /* storage blocked */ }
  return null;
}

function authHeaders() {
  const token = authToken();
  return token ? { Authorization: 'Bearer ' + token } : {};
}

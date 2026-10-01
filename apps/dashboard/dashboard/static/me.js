// Who is signed in (set by the proxy's staff login). Pages use the body's data-role / data-signed-in
// attributes to hide what this person can't do; the server enforces the same rules.
window.me = fetch("api/me").then((r) => r.json()).catch(() => ({name: null, role: "manager", is_manager: true}));
window.me.then((m) => {
  const apply = () => {
    document.body.dataset.role = m.role;
    if (m.name) document.body.dataset.signedIn = "1";
    const el = document.getElementById("me");
    if (el && m.name) el.textContent = `Signed in as ${m.name}${m.is_manager ? " · manager" : ""}`;
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply); else apply();
});

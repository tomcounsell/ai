// In-page tabs: underline the tab whose section is in view.
const tabs = [...document.querySelectorAll("nav.subnav.sections a")];
const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    for (const a of tabs) {
      if (a.hash === "#" + entry.target.id) a.setAttribute("aria-current", "location");
      else a.removeAttribute("aria-current");
    }
  }
}, { rootMargin: "-120px 0px -60% 0px" });
for (const a of tabs) observer.observe(document.querySelector(a.hash));

// A link button beside each section label copies the URL of that section.
const ICON = '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M7.78 3.22a.75.75 0 0 1 0 1.06L6.56 5.5a2.25 2.25 0 0 0 3.18 3.18l1.22-1.22a.75.75 0 1 1 1.06 1.06l-1.22 1.22a3.75 3.75 0 0 1-5.3-5.3l1.22-1.22a.75.75 0 0 1 1.06 0Zm4.44-.22a3.75 3.75 0 0 1 0 5.3.75.75 0 0 1-1.06-1.06 2.25 2.25 0 0 0-3.18-3.18.75.75 0 1 1-1.06-1.06 3.75 3.75 0 0 1 5.3 0ZM4.5 11.5a.75.75 0 0 1 0-1.06l6-6a.75.75 0 1 1 1.06 1.06l-6 6a.75.75 0 0 1-1.06 0Z"/></svg>';
for (const label of document.querySelectorAll("section[id] > .sec-label")) {
  const id = label.parentElement.id;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-link";
  button.title = "Copy link to this section";
  button.setAttribute("aria-label", "Copy link to this section");
  button.innerHTML = ICON;
  button.addEventListener("click", async () => {
    const url = location.href.split("#")[0] + "#" + id;
    await navigator.clipboard.writeText(url);
    history.replaceState(null, "", "#" + id);
    button.classList.add("copied");
    button.title = "Copied";
    setTimeout(() => { button.classList.remove("copied"); button.title = "Copy link to this section"; }, 1500);
  });
  label.append(button);
}

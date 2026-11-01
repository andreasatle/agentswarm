// The studio's few behaviours: parts of a page that refresh while a step runs, the cost plan as the form
// changes, the drop zone, and a confirmation before a step that can't be undone.
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function refresh(element) {
  // data-refresh: the URL of the part; data-replace="outer" when the part answers with the element itself
  const every = Number(element.dataset.every || 3000);
  setTimeout(async () => {
    if (!document.body.contains(element)) return;
    if (element.contains(document.activeElement) && document.activeElement !== document.body) {
      return refresh(element); // never replace a field someone is typing in
    }
    try {
      const reply = await fetch(element.dataset.refresh);
      if (reply.ok) {
        const text = await reply.text();
        if (element.dataset.replace === "outer") {
          const holder = document.createElement("div");
          holder.innerHTML = text;
          const fresh = holder.firstElementChild;
          element.replaceWith(fresh);
          wire(fresh); // refreshes on only while the run is busy
        } else {
          element.innerHTML = text;
          wire(element);
          refresh(element);
        }
        return;
      }
    } catch (error) { /* the studio restarting: try again */ }
    refresh(element);
  }, every);
}

function plan(form) {
  const box = document.querySelector(`[data-plan-of="${form.id}"]`);
  if (!box) return;
  let timer = null;
  const update = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const query = new URLSearchParams({ words: form.words.value, budget: form.budget.value });
      const reply = await fetch("/plan?" + query);
      if (reply.ok) box.innerHTML = await reply.text();
    }, 200);
  };
  form.words.addEventListener("input", update);
  form.budget.addEventListener("input", update);
}

function slug(input) {
  const source = input.form.elements[input.dataset.slugOf];
  const update = () => {
    const name = source.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/, "");
    input.placeholder = name || "from the topic";
  };
  source.addEventListener("input", update);
  update();
}

function drop(form) {
  const input = form.querySelector("input[type=file]");
  input.addEventListener("change", () => input.files.length && form.submit());
  ["dragenter", "dragover"].forEach((type) => form.addEventListener(type, (event) => { event.preventDefault(); form.classList.add("over"); }));
  ["dragleave", "drop"].forEach((type) => form.addEventListener(type, () => form.classList.remove("over")));
  form.addEventListener("drop", (event) => {
    event.preventDefault();
    if (event.dataTransfer.files.length) { input.files = event.dataTransfer.files; form.submit(); }
  });
}

function wire(root = document) {
  if (root !== document && root.matches("[data-refresh]")) refresh(root);
  $$("[data-refresh]", root).forEach(refresh);
  $$("input[data-slug-of]", root).forEach(slug);
  $$("form.drop", root).forEach(drop);
  if (root === document) $$("form#new").forEach(plan);
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-confirm]");
  if (button && !confirm(button.dataset.confirm)) event.preventDefault();
});
document.addEventListener("submit", (event) => {
  // one press is one answer: a second press while the page loads would answer twice
  const button = event.submitter;
  if (button && !event.defaultPrevented) setTimeout(() => { button.disabled = true; }, 0);
});
wire();

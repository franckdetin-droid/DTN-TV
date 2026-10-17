document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('form[enctype="multipart/form-data"]').forEach(form => {
    form.addEventListener("change", event => {
      const input = event.target;
      if (input instanceof HTMLInputElement && input.type === "file" && input.files && input.files[0]) {
        const file = input.files[0];
        if (file.size > 5 * 1024 * 1024) {
          alert("L'image dépasse la limite de 5 Mo.");
          input.value = "";
        }
      }
    });
  });
});

  const carousel = document.querySelector("[data-carousel]");
  if (carousel) {
    const slides = Array.from(carousel.querySelectorAll(".featured-slide"));
    const dotsWrap = document.querySelector("[data-carousel-dots]");
    let current = 0;
    let timer = null;
    const show = (index) => {
      current = (index + slides.length) % slides.length;
      slides.forEach((slide, i) => slide.classList.toggle("is-active", i === current));
      if (dotsWrap) {
        dotsWrap.querySelectorAll("button").forEach((dot, i) => {
          dot.classList.toggle("active", i === current);
          dot.setAttribute("aria-current", i === current ? "true" : "false");
        });
      }
    };
    if (dotsWrap) {
      slides.forEach((_, i) => {
        const dot = document.createElement("button");
        dot.type = "button";
        dot.className = "carousel-dot";
        dot.setAttribute("aria-label", `Afficher la diapositive ${i + 1}`);
        dot.addEventListener("click", () => { show(i); restart(); });
        dotsWrap.appendChild(dot);
      });
    }
    const restart = () => {
      if (timer) clearInterval(timer);
      if (slides.length > 1) timer = setInterval(() => show(current + 1), 5000);
    };
    document.querySelector("[data-carousel-prev]")?.addEventListener("click", () => { show(current - 1); restart(); });
    document.querySelector("[data-carousel-next]")?.addEventListener("click", () => { show(current + 1); restart(); });
    show(0);
    restart();
  }

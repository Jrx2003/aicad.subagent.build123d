const navDots = Array.from(document.querySelectorAll(".nav-dot"));
const sections = navDots
  .map((dot) => document.getElementById(dot.dataset.target))
  .filter(Boolean);
const progressBar = document.getElementById("progress-bar");
const revealNodes = document.querySelectorAll(".reveal");

const revealObserver = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("visible");
        revealObserver.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.14 }
);

revealNodes.forEach((node) => revealObserver.observe(node));

function updateProgress() {
  const scrollTop = window.scrollY;
  const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
  const ratio = maxScroll <= 0 ? 0 : Math.min(1, Math.max(0, scrollTop / maxScroll));
  progressBar.style.width = `${ratio * 100}%`;

  let activeId = sections[0]?.id;
  sections.forEach((section) => {
    const rect = section.getBoundingClientRect();
    if (rect.top <= window.innerHeight * 0.32 && rect.bottom >= window.innerHeight * 0.2) {
      activeId = section.id;
    }
  });

  navDots.forEach((dot) => {
    dot.classList.toggle("active", dot.dataset.target === activeId);
  });
}

navDots.forEach((dot) => {
  dot.addEventListener("click", () => {
    document.getElementById(dot.dataset.target)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

window.addEventListener("scroll", updateProgress, { passive: true });
window.addEventListener("resize", updateProgress);
updateProgress();

const sections = Array.from(document.querySelectorAll(".module"));
const navDots = Array.from(document.querySelectorAll(".nav-dot"));
const progressBar = document.getElementById("progress-bar");
const reveals = Array.from(document.querySelectorAll(".reveal"));
const artifactTabs = Array.from(document.querySelectorAll(".artifact-tab"));
const artifactPanels = Array.from(document.querySelectorAll(".artifact-panel"));

function setActiveSection(sectionId) {
  navDots.forEach((dot) => {
    const isActive = dot.dataset.target === sectionId;
    dot.classList.toggle("active", isActive);
    dot.setAttribute("aria-selected", isActive ? "true" : "false");
  });
}

function updateProgress() {
  const documentHeight = document.documentElement.scrollHeight - window.innerHeight;
  const progress = documentHeight <= 0 ? 0 : (window.scrollY / documentHeight) * 100;
  progressBar.style.width = `${Math.min(100, Math.max(0, progress))}%`;

  let currentSection = sections[0];
  for (const section of sections) {
    const rect = section.getBoundingClientRect();
    if (rect.top <= window.innerHeight * 0.32) {
      currentSection = section;
    }
  }
  if (currentSection) {
    setActiveSection(currentSection.id);
  }
}

const revealObserver = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
      }
    });
  },
  {
    threshold: 0.16,
    rootMargin: "0px 0px -8% 0px",
  },
);

reveals.forEach((item, index) => {
  item.style.transitionDelay = `${Math.min(index % 6, 5) * 45}ms`;
  revealObserver.observe(item);
});

navDots.forEach((dot) => {
  dot.addEventListener("click", () => {
    const target = document.getElementById(dot.dataset.target);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
});

artifactTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    const targetId = tab.dataset.artifactTarget;
    artifactTabs.forEach((item) => item.classList.toggle("active", item === tab));
    artifactPanels.forEach((panel) =>
      panel.classList.toggle("active", panel.id === targetId),
    );
  });
});

window.addEventListener("scroll", updateProgress, { passive: true });
window.addEventListener("resize", updateProgress);
updateProgress();

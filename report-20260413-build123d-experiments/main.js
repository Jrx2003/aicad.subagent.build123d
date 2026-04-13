const observer = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
      }
    }
  },
  {
    threshold: 0.16,
    rootMargin: "0px 0px -8% 0px",
  },
);

for (const section of document.querySelectorAll("[data-reveal]")) {
  observer.observe(section);
}

const navLinks = [...document.querySelectorAll(".chapter-nav a")];
const sections = navLinks
  .map((link) => document.querySelector(link.getAttribute("href")))
  .filter(Boolean);

const navObserver = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) {
        continue;
      }
      const id = `#${entry.target.id}`;
      for (const link of navLinks) {
        link.classList.toggle("is-active", link.getAttribute("href") === id);
      }
    }
  },
  {
    threshold: 0.45,
  },
);

for (const section of sections) {
  navObserver.observe(section);
}

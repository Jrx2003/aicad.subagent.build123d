document.documentElement.classList.add("js");

const revealTargets = document.querySelectorAll(".hero, .card");

if ("IntersectionObserver" in window) {
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.12 }
  );

  revealTargets.forEach((node) => observer.observe(node));
} else {
  revealTargets.forEach((node) => node.classList.add("is-visible"));
}

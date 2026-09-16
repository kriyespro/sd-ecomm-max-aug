/* Spotlight walkthrough for Mission Control pages — Alpine component.
 * Highlights a real element via [data-tour="<target>"] with a CSS
 * box-shadow cutout (no SVG mask), a tooltip card with Next/Back/Skip.
 * No "seen" tracking — Profile.show_guides is the only on/off state. */
function pageTour(steps) {
  return {
    steps: steps || [],
    i: 0,
    active: false,
    rect: null,

    init() {
      if (!this.steps.length) return;
      this.active = true;
      this.measure();
    },

    get step() {
      return this.steps[this.i] || {};
    },

    measure() {
      this.rect = null;
      const el = document.querySelector(`[data-tour="${this.step.target}"]`);
      if (!el) {
        // target not on screen (e.g. an empty state hid the button) — skip it
        this.next();
        return;
      }
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      setTimeout(() => {
        const r = el.getBoundingClientRect();
        this.rect = {
          top: r.top + window.scrollY, left: r.left + window.scrollX,
          width: r.width, height: r.height,
        };
      }, 350);
    },

    tooltipStyle() {
      if (!this.rect) return "top:40vh;left:calc(50vw - 9rem);";
      const cardW = 288;
      const top = this.rect.top + this.rect.height + 14;
      const left = Math.max(
        12, Math.min(this.rect.left, window.scrollX + window.innerWidth - cardW - 12)
      );
      return `top:${top}px;left:${left}px;`;
    },

    next() {
      if (this.i < this.steps.length - 1) {
        this.i++;
        this.measure();
      } else {
        this.finish();
      }
    },

    back() {
      if (this.i > 0) {
        this.i--;
        this.measure();
      }
    },

    finish() {
      this.active = false;
    },
  };
}

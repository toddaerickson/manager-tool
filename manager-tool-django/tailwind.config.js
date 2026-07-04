/* Design tokens — moved verbatim from the inline tailwind.config in
 * templates/base.html when the Play CDN was replaced by a compiled
 * build (roadmap PR 2). One accent (teal), a deliberate type pair,
 * and a tightened radius scale; see DESIGN.md for the rules.
 *
 * Build (Tailwind CLI v3 — v4 changed the config model; keep pinned):
 *   TAILWINDCSS_VERSION=v3.4.17 tailwindcss -c tailwind.config.js \
 *     -i static/src/input.css -o static/css/tw.css --minify
 *
 * content includes .py files: core/forms.py widget attrs and
 * coaching/services.py emit Tailwind classes from Python strings.
 */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./core/**/*.py",
    "./coaching/**/*.py",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Public Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['Fraunces', 'ui-serif', 'Georgia', 'serif'],
      },
      colors: {
        accent: {
          50: '#f0fdfa', 100: '#ccfbf1', 200: '#99f6e4', 300: '#5eead4',
          400: '#2dd4bf', 500: '#14b8a6', 600: '#0d9488', 700: '#0f766e',
          800: '#115e59', 900: '#134e4a',
        },
      },
      borderRadius: {
        DEFAULT: '0.25rem', md: '0.375rem', lg: '0.375rem', xl: '0.5rem',
      },
    },
  },
};

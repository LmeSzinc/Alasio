/** @type {import('tailwindcss').Config} */
export default {
  content: ["./src/**/*.{html,js,svelte,ts}"],
  theme: {
    extend: {
      fontFamily: {
        'consolas': ['Consolas', 'Courier New', 'monospace'],
      },
    },
  },
  plugins: [],
};

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#f0f4f8',
          100: '#d9e2ec',
          200: '#b2c9de',
          300: '#8ab0d0',
          400: '#6297c2',
          500: '#1f2a44',
          600: '#1a2235',
          700: '#151b2a',
          800: '#0f131f',
          900: '#0a0c14',
        },
      },
    },
  },
  plugins: [],
}

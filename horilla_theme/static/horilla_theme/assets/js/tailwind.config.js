window.tailwind.config = {
  theme: {
    colors: {
      white: '#FFFFFF',
      primary: {
        50: '#F4F6FA',
        100: '#EAF3FF',
        200: '#D7E9FF',
        300: '#B8D8FF',
        400: '#8FC2FF',
        500: '#5AA6FF',
        600: '#2D8CFF',
        700: '#1C70D9',
        800: '#1254AA',
        900: '#0F2D5B',
      },

      dark: {
        50: '#F4F6FA',
        100: '#D6DFEB',
        200: '#9AAFC9',
        300: '#6D829E',
        400: '#536987',
        500: '#2C466B',
        600: '#0F2D5B',
      },

      secondary: {
        50: '#F0FCFA',
        100: '#E3F8F5',
        200: '#C8F1EC',
        300: '#A1E7DF',
        400: '#75D9CD',
        500: '#39C8B9',
        600: '#14B8A6',
        700: '#0E9487',
        800: '#0C756B',
        900: '#07584F',
      },
      success: {
        light: "#86efac",
        DEFAULT: "#22c55e",
        dark: "#15803d",
      },
      warning: {
        light: "#fde68a",
        DEFAULT: "#f59e0b",
        dark: "#b45309",
      },
      danger: {
        light: "#fca5a5",
        DEFAULT: "#ef4444",
        dark: "#b91c1c",
      },

      brand: {
        200: '#D7E9FF',
        300: '#B8D8FF',
        500: '#5AA6FF',
        600: '#2D8CFF',
        700: '#1C70D9',
        800: '#1254AA',
      },

      color: {
        600: '#374151',
      },
    },
    extend: {
      boxShadow: {
        card: "0px 0px 10px rgba(0, 0, 0, 0.05)",
      },
      spacing: {
        18: "4.5rem",
        72: "18rem",
        84: "21rem",
        96: "24rem",
      },
      borderRadius: {
        "4xl": "2rem",
        "5xl": "2.5rem",
      },
      fontSize: {
        xxs: "0.625rem",
      },
      height: {
        "screen-50": "50vh",
        "screen-75": "75vh",
      },
    },
  },
};

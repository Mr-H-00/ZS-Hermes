import { defineConfig, globalIgnores } from 'eslint/config'
import globals from 'globals'
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tsParser from '@typescript-eslint/parser'
import { frontendBoundaries } from './eslint-boundaries.js'
import skipFormatting from '@vue/eslint-config-prettier/skip-formatting'

export default defineConfig([
  {
    name: 'app/files-to-lint',
    files: ['**/*.{vue,js,mjs,jsx,ts}']
  },

  globalIgnores(['**/dist/**', '**/dist-ssr/**', '**/coverage/**']),

  {
    languageOptions: {
      globals: {
        ...globals.browser
      }
    }
  },

  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],

  {
    files: ['src/**/*.{js,ts,vue}'],
    plugins: { architecture: { rules: { boundaries: frontendBoundaries } } },
    rules: { 'architecture/boundaries': 'error' }
  },
  {
    files: ['**/*.ts'],
    languageOptions: { parser: tsParser },
    rules: { 'no-unused-vars': 'off' }
  },
  skipFormatting
])

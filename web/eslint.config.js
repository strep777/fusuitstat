// ESLint 扁平配置（04 文档：Ruff、mypy、ESLint、vue-tsc 作为工程质量门禁）。
// 只做静态质量与一致性检查，不接管类型检查（由 vue-tsc 负责）。

import globals from "globals";
import js from "@eslint/js";
import vue from "eslint-plugin-vue";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [
      "dist/**",
      "node_modules/**",
      "public/**",
      "coverage/**",
      "*.config.js",
      "tests/**",
    ],
  },
  js.configs.recommended,
  { files: ["src/**/*.{vue,ts}"], languageOptions: { globals: globals.browser } },
  ...tseslint.configs.recommended,
  ...vue.configs["flat/recommended"],
  {
    files: ["**/*.vue"],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
        ecmaVersion: "latest",
        sourceType: "module",
        extraFileExtensions: [".vue"],
      },
    },
    rules: {
      // 组件名采用多词（App 例外），与 Vue 官方风格指南一致。
      "vue/multi-word-component-names": ["error", { ignores: ["App"] }],
      "vue/component-name-in-template-casing": ["error", "PascalCase"],
      "vue/no-v-html": "off", // 正文由服务端白名单清洗，见 10 文档
      "vue/require-default-prop": "off",
      "vue/attributes-order": "warn",
      "vue/max-attributes-per-line": "off",
      "vue/singleline-html-element-content-newline": "off",
      "vue/html-self-closing": "off",
    },
  },
  {
    files: ["**/*.ts"],
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/consistent-type-imports": ["warn", { prefer: "type-imports" }],
      "no-console": ["warn", { allow: ["warn", "error"] }],
    },
  },
);

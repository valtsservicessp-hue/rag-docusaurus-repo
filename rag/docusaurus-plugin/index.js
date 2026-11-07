// Local Docusaurus plugin. Its theme folder supplies @theme/Root, which wraps
// every page, so the chat appears site-wide without editing anything in src/.
module.exports = function docsChatPlugin() {
  return {
    name: 'docs-chat',
    getThemePath() {
      return './theme';
    },
  };
};

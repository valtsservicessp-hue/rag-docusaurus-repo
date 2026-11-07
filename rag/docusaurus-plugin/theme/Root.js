import React from 'react';
import DocsChat from '@theme/DocsChat';

// Root stays mounted across page changes, so the conversation survives
// following a citation link.
export default function Root({children}) {
  return (
    <>
      {children}
      <DocsChat />
    </>
  );
}

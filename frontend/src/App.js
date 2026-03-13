import { useEffect } from "react";

// XI Ventures is a static website served from public/index.html
// This React app is minimal - it just ensures the static HTML is served correctly

function App() {
  useEffect(() => {
    // The static XI Ventures website handles everything
    // This React wrapper is kept minimal
  }, []);

  // Return null - the static HTML in public/index.html handles the UI
  return null;
}

export default App;

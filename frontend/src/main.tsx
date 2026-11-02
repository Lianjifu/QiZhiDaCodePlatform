import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'

import App from './App'
import { SessionsProvider } from './features/sessions/store'
import { ThemeProvider } from './theme/ThemeProvider'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <SessionsProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </SessionsProvider>
    </ThemeProvider>
  </StrictMode>,
)

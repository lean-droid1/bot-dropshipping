import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// Fix viewport iOS: en Safari la barra de direcciones "come" el 100vh y corta el contenido
// o deja botones fuera de alcance. Seteamos --app-vh con la altura visible real
// (funciona en TODAS las versiones de iOS, no solo 15.4+).
const setAppVh = () => document.documentElement.style.setProperty('--app-vh', (window.innerHeight * 0.01) + 'px');
setAppVh();
window.addEventListener('resize', setAppVh);
window.addEventListener('orientationchange', setAppVh);

ReactDOM.createRoot(document.getElementById('root')).render(<App />)

/**
 * ErrorBoundary.jsx — Isola erros de render/lifecycle por se\u00e7\u00e3o.
 *
 * Class component porque React hooks ainda n\u00e3o suportam error boundaries.
 * Fallback UI com bot\u00e3o de retry. Em dev mostra componentStack via <details>.
 */
import { Component } from 'react'
import './ErrorBoundary.css'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null, info: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // Log pro console — em producao aparece nos logs do browser
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', this.props.label ?? 'unlabeled', error, info)
    this.setState({ info })
  }

  handleReset = () => {
    this.setState({ error: null, info: null })
  }

  render() {
    if (!this.state.error) return this.props.children

    const label = this.props.label ?? 'Seção com erro'
    const isDev = import.meta.env.DEV

    return (
      <div className="eb" role="alert">
        <div className="eb__icon" aria-hidden="true">⚠️</div>
        <div className="eb__title">{label}</div>
        <div className="eb__msg">
          {this.state.error.message ?? 'Erro desconhecido'}
        </div>
        <button className="eb__retry" type="button" onClick={this.handleReset}>
          Tentar novamente
        </button>
        {isDev && this.state.info && (
          <details className="eb__details">
            <summary>Stack trace (dev)</summary>
            <pre>{this.state.info.componentStack}</pre>
          </details>
        )}
      </div>
    )
  }
}

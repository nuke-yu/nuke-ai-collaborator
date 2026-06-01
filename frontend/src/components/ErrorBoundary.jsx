import React from 'react'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    console.error("Uncaught error:", error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen bg-gray-900 flex flex-col items-center justify-center p-6 text-center">
          <div className="text-5xl mb-4">😵</div>
          <h1 className="text-white text-xl font-bold mb-2">应用出错了</h1>
          <p className="text-gray-400 text-sm mb-6 max-w-md">
            发生了一个意外错误，这可能是前端渲染 Bug 或网络抖动导致的。
          </p>
          <pre className="bg-black/40 text-red-400 p-4 rounded-lg text-xs mb-6 max-w-full overflow-auto text-left">
            {this.state.error?.toString()}
          </pre>
          <button
            onClick={() => window.location.reload()}
            className="bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-6 py-2 text-sm font-medium transition-colors"
          >
            刷新页面
          </button>
        </div>
      )
    }

    return this.props.children
  }
}

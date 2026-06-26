import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import ErrorBoundary from './ErrorBoundary'

const ProblematicComponent = () => {
  throw new Error('Test Error')
}

describe('ErrorBoundary', () => {
  it('renders children when there is no error', () => {
    render(
      <ErrorBoundary>
        <div>Normal Content</div>
      </ErrorBoundary>
    )
    expect(screen.getByText('Normal Content')).toBeInTheDocument()
  })

  it('renders error UI when child throws', () => {
    // Suppress console.error for this test as we expect an error
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    
    render(
      <ErrorBoundary>
        <ProblematicComponent />
      </ErrorBoundary>
    )
    
    expect(screen.getByText('页面出错了')).toBeInTheDocument()
    expect(screen.getByText(/Test Error/)).toBeInTheDocument()
    
    spy.mockRestore()
  })
})

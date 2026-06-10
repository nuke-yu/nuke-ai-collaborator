import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import ThinkingSection from './ThinkingSection'

const blocks = {
  1: { iteration: 1, content: 'step one', completed: true },
  2: { iteration: 2, content: 'step two', completed: false },
}

describe('ThinkingSection (Option B)', () => {
  it('renders nothing when there are no blocks', () => {
    const { container } = render(<ThinkingSection blocks={{}} streaming={true} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('while streaming shows only the current (latest) step, expanded', () => {
    render(<ThinkingSection blocks={blocks} streaming={true} />)
    expect(screen.getByText('iteration 2')).toBeInTheDocument()
    expect(screen.getByText('step two')).toBeInTheDocument()
    // earlier step is hidden — no wall of boxes
    expect(screen.queryByText('iteration 1')).not.toBeInTheDocument()
    expect(screen.queryByText('step one')).not.toBeInTheDocument()
  })

  it('after the turn shows a single collapsed "已思考 N 步" summary', () => {
    render(<ThinkingSection blocks={blocks} streaming={false} />)
    expect(screen.getByText('已思考 2 步')).toBeInTheDocument()
    // steps hidden until expanded
    expect(screen.queryByText('step one')).not.toBeInTheDocument()
    expect(screen.queryByText('step two')).not.toBeInTheDocument()
  })

  it('clicking the summary expands every step', () => {
    render(<ThinkingSection blocks={blocks} streaming={false} />)
    fireEvent.click(screen.getByText('已思考 2 步'))
    expect(screen.getByText('step one')).toBeInTheDocument()
    expect(screen.getByText('step two')).toBeInTheDocument()
    expect(screen.getByText('iteration 1')).toBeInTheDocument()
    expect(screen.getByText('iteration 2')).toBeInTheDocument()
  })
})

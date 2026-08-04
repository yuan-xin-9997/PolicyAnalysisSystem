import { render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import PlaceholderView from '../../app/frontend/src/views/PlaceholderView.vue'

describe('占位页面', () => {
  it('推送和分析页面只展示后续规划，不提供发送、分析或预测按钮', async () => {
    const { rerender } = render(PlaceholderView, { props: { page: 'push' } })
    expect(screen.getByText('后续将规划按政策类别和发布部门进行邮件推送。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /发送|推送/ })).not.toBeInTheDocument()

    await rerender({ page: 'analysis' })
    expect(screen.getByText('后续将规划词频、表述差异与大模型辅助研判能力。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /分析|预测/ })).not.toBeInTheDocument()
  })
})

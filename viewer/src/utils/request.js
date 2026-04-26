/**
 * request.js — viewer 专用 axios 实例
 *
 * 参考同事的 request.js 风格，但去掉了 viewer 不需要的部分：
 *   - 无 token / 登录跳转（内部工具）
 *   - 无 Element Plus（未安装）
 *   - 无重复提交拦截
 *
 * 与同事版本的关键区别：
 *   arraybuffer 响应返回完整 res 对象（而非只返回 res.data），
 *   这样调用方可以同时拿到二进制数据和 X-* 自定义 Header。
 */
import axios from 'axios'

const http = axios.create({
  timeout: 5 * 60 * 1000,
  headers: {
    'Content-Type': 'application/json;charset=UTF-8',
  }
})

// 请求拦截器（目前透传，预留扩展位置）
http.interceptors.request.use(
  config => config,
  error => Promise.reject(error)
)

// 响应拦截器
http.interceptors.response.use(
  res => {
    // arraybuffer 响应：返回完整 res，调用方自己取 res.data 和 res.headers
    // （如果只返回 res.data 会丢失 X-Val-Min / X-Face-Count 等元数据 Header）
    if (res.config.responseType === 'arraybuffer') {
      return res
    }
    // JSON 响应：直接返回数据，与同事版本行为一致
    return res.data
  },
  error => {
    const res = error.response
    if (res) {
      const msg = res.data?.message || res.data?.detail || '服务连接异常'
      console.error(`[ODB API ${res.status}]`, msg)
      return Promise.reject(new Error(msg))
    }
    console.error('[ODB API]', error.message)
    return Promise.reject(error)
  }
)

export default http

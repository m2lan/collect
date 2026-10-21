import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router'

const http = axios.create({ baseURL: '/api', timeout: 180000 })

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (resp) => resp.data,
  (error) => {
    const status = error.response?.status
    if (status === 401 && router.currentRoute.value.name !== 'login') {
      localStorage.removeItem('token')
      router.push({ name: 'login' })
    }
    ElMessage.error(errorText(error))
    return Promise.reject(error)
  },
)

export function errorText(error) {
  const detail = error.response?.data?.detail
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('；')
  }
  return detail || error.message
}

export default http

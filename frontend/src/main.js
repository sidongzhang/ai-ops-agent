import { createApp } from 'vue'
import { createPinia } from 'pinia'
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Card,
  Col,
  ConfigProvider,
  Divider,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Pagination,
  Segmented,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Row,
  Select,
  Spin,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
} from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)

;[
  Alert,
  Button,
  Checkbox,
  Collapse,
  Card,
  Col,
  ConfigProvider,
  Divider,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Pagination,
  Segmented,
  Modal,
  Popconfirm,
  Progress,
  Radio,
  Row,
  Select,
  Spin,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
].forEach((component) => app.use(component))

app.use(createPinia()).use(router).mount('#app')

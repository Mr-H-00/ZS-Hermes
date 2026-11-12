import { defineComponent, h } from 'vue'
import { Empty } from 'ant-design-vue'
import { Inbox } from '@lucide/vue'

/** 渲染不承载额外语义的默认空状态图标。 */
function renderDefaultImage() {
  return h(Inbox, {
    class: 'yuxi-empty__icon',
    size: 32,
    strokeWidth: 1.5,
    'aria-hidden': 'true'
  })
}

const AntEmpty = defineComponent({
  name: 'YuxiEmpty',
  inheritAttrs: false,
  props: Empty.props,
  /** 只替换缺省图像，保留原 Empty 的内容与定制接口。 */
  setup(props, { attrs, slots }) {
    return () => {
      const defaultImage = props.image === undefined && !slots.image
      return h(
        Empty,
        {
          ...props,
          ...attrs,
          class: [
            'yuxi-empty',
            {
              'yuxi-empty--default': defaultImage,
              'yuxi-empty--no-image': props.image === false
            },
            attrs.class
          ]
        },
        defaultImage ? { ...slots, image: renderDefaultImage } : slots
      )
    }
  }
})

/** 让列表与菜单共享同一空状态，菜单使用紧凑间距。 */
export function renderEmpty(componentName) {
  const compact = ['Select', 'TreeSelect', 'Cascader', 'Transfer', 'Mentions'].includes(
    componentName
  )
  return h(AntEmpty, { class: { 'yuxi-empty--compact': compact } })
}

export default AntEmpty

/** 判断面包屑是否可以接收拖放。 */
export const canDropOnFileBreadcrumb = ({ enabled, item, index, count }) =>
  Boolean(enabled && item && !item.dropDisabled && index !== count - 1)

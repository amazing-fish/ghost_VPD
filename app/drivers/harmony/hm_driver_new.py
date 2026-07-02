from hmdriver2.proto import Bounds
from hmdriver2.utils import parse_bounds
from hmdriver2.driver import Driver


class HMDriverNew(Driver):

    def __init__(self, device_sn: str):
        super().__init__(device_sn)
        self._hierarchy = {}
        self._xml = None

    def _set_xml(self):
        self._xml = self.xpath._json2xml(self._hierarchy)

    def dump_hierarchy(self) -> dict:
        hierarchy = super().dump_hierarchy()
        self._hierarchy = hierarchy
        self._set_xml()
        return hierarchy

    def xpath_get_attrib(self, xpath: str, if_fresh: bool = True):
        if if_fresh:  # 刷新图层
            self.dump_hierarchy()

        attrib_dict = {}
        if self._xml is not None:
            result = self._xml.xpath(xpath)
            if len(result) > 0:
                node = result[0]
                attrib_dict = dict(node.attrib)
        return attrib_dict

    def xpath_exists(self, xpath: str, refresh: bool = True):
        if refresh:
            self.dump_hierarchy()
        coordinate = {}
        attrib_dict = self.xpath_get_attrib(xpath)
        if attrib_dict:
            raw_bounds: str = attrib_dict.get("bounds")
            bounds: Bounds = parse_bounds(raw_bounds)
            coordinate = bounds.get_center().to_dict()
        return coordinate

    def xpath_select(self, xpath: str):
        return self.xpath(xpath)

    def ui_select(self, **kwargs):
        return self(**kwargs)

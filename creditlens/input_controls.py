"""Fixed synthetic raw-input controls; never real issuer OCR or human gold."""
from copy import deepcopy


def raw_fixture():
    quad = [[20, 20], [40, 20], [40, 30], [20, 30]]
    return {"schema_version": 1, "page": 1, "width": 100, "height": 100,
            "image_width": 100, "image_height": 100,
            "result": {"boxes": [quad], "txts": ["100"], "scores": [0.99],
                       "word_results": [[["100", 0.99, deepcopy(quad)]]]}}


def _set(value, path, item):
    target = value
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = deepcopy(item)


def raw_control_cases():
    # Expectations are fixed requirements, not generated from candidate output.
    specs = [
        ("normal", "正常矩形", True, []),
        ("rotated", "合法旋转四边形", True, [(('result','boxes',0),[[20,20],[40,25],[38,35],[18,30]]),
                    (('result','word_results',0,0,2),[[20,20],[40,25],[38,35],[18,30]])]),
        ("reverse", "合法反向顶点顺序", True, [(('result','boxes',0),[[20,30],[40,30],[40,20],[20,20]])]),
        ("explicit_empty", "明确空输出", True, [(('result',),dict(boxes=[],txts=[],scores=[],word_results=[]))]),
        ("all_none", "四字段全空", True, [(('result',),dict(boxes=None,txts=None,scores=None,word_results=None))]),
        ("engine_empty", "已核验引擎空输出结构", True, [(('result',),dict(boxes=None,txts=None,scores=None,word_results=[["",1.0,None]]))]),
        ("nan_corner", "第三角含非有限数", False, [(('result','boxes',0,2,0),float('nan'))]),
        ("inf_corner", "角点含无穷大", False, [(('result','boxes',0,2,1),float('inf'))]),
        ("bool_corner", "角点是布尔值", False, [(('result','boxes',0,2,0),True)]),
        ("string_corner", "角点是数字字符串", False, [(('result','boxes',0,2,0),'40')]),
        ("negative_corner", "角点越出左边界", False, [(('result','boxes',0,0,0),-1)]),
        ("outside_corner", "角点越出图像", False, [(('result','boxes',0,2,0),101)]),
        ("zero_area", "四角退化成直线", False, [(('result','boxes',0),[[20,20],[30,20],[40,20],[50,20]])]),
        ("self_crossing", "顶点连线自交", False, [(('result','boxes',0),[[20,20],[40,30],[40,20],[20,30]])]),
        ("concave", "不支持的凹四边形", False, [(('result','boxes',0),[[20,20],[40,20],[25,25],[20,30]])]),
        ("missing_corner", "少一个角点", False, [(('result','boxes',0),[[20,20],[40,20],[40,30]])]),
        ("word_nan", "字词框含非有限数", False, [(('result','word_results',0,0,2,2,0),float('nan'))]),
        ("score_inf", "识别分数含无穷大", False, [(('result','scores',0),float('inf'))]),
        ("score_range", "识别分数越界", False, [(('result','scores',0),1.2)]),
        ("text_type", "文字字段类型错误", False, [(('result','txts',0),100)]),
        ("line_count", "行数组长度不一致", False, [(('result','txts',),[])]),
        ("missing_words", "有文字行但词数组未知", False, [(('result','word_results',0),None)]),
        ("orphan_text", "没有框却残留文字", False, [(('result','boxes',),None)]),
        ("zero_dimension", "图像宽度为零", False, [(('image_width',),0)]),
        ("bool_dimension", "图像宽度是布尔值", False, [(('image_width',),True)]),
    ]
    cases=[]
    for identity, title, accepted, changes in specs:
        raw=raw_fixture()
        for path,value in changes:
            _set(raw,path,value)
        cases.append({"id":identity,"title":title,"expected_accepted":accepted,"raw":raw})
    return cases


def printable(value):
    """Represent non-finite test mutations as tagged data in strict JSON."""
    import math
    if isinstance(value,float) and not math.isfinite(value):
        return {"synthetic_nonfinite":'NaN' if math.isnan(value) else 'Infinity' if value>0 else '-Infinity'}
    if isinstance(value,dict):
        return {key:printable(item) for key,item in value.items()}
    if isinstance(value,(list,tuple)):
        return [printable(item) for item in value]
    return value

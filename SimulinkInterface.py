import shutil
import xml.etree.ElementTree as eT
import os
import zipfile
import pydot
from graphviz import Source


class SimulinkModel:
    def __init__(self, model_path):
        self.__model_path = model_path
        self.__tempFolderPath = os.path.join(os.getcwd(), "temp")
        file_path_list = self.__util_unzip_files()
        for file_path in file_path_list:
            if file_path.endswith("system_root.xml"):
                self.tree = eT.parse(file_path)
                break
        sp = SimulinkParser(self.tree, self.__tempFolderPath)
        if os.path.isdir(self.__tempFolderPath):
            shutil.rmtree(self.__tempFolderPath)
        self.block_list = sp.blocks
        self.connection_list = sp.connections
        self.grapher_instance = Grapher(self.block_list,self.connection_list)

    def __util_unzip_files(self):
        file_path_list = []

        with zipfile.ZipFile(self.__model_path, 'r') as zip_ref:
            zip_ref.extractall(self.__tempFolderPath)
            extracted_files = zip_ref.filelist

        for file in extracted_files:
            file_path_list.append(os.path.join(self.__tempFolderPath, file.filename.replace("/", "\\")))

        return file_path_list

class SimulinkParser:
    def __init__(self,input_tree,temp_folder_path):
        self.tree = input_tree
        self.tempFolderPath = temp_folder_path
        self.blocks,self.connections = self.__util_parse_tree(self.tree.getroot())

    def __util_parse_tree(self,element,parent="root"):
        block_list = element.findall("Block")
        conn_list = self.__util_find_conns(element)
        new_block_list = []
        for block in block_list:
            simulink_block = self.__util_blk_info(block)
            simulink_block["Parent_SID"] = parent
            if simulink_block not in new_block_list:
                new_block_list.append(simulink_block)
        return new_block_list,conn_list

    def __util_blk_info(self, block):
        temp = block.__copy__()
        # Collect the first set of attributes of the Simulink block
        temp = temp.attrib
        # Get all parameters of the Block
        parameters = block.findall("P")
        # Get Mask of the Block
        mask_detection = block.find("Mask")
        # Get link to another system, if Subsystem Block
        system_ref_detect = block.find("System")
        # Get Port Details
        port_detect = block.find("Port")
        for parameter in parameters:
            temp[list(parameter.attrib.values())[0]] = parameter.text
        if mask_detection is not None:
            if mask_detection.find("Type") is not None:
                temp["Mask_Type"] = mask_detection.find("Type").text
            if mask_detection.find("Help") is not None:
                temp["Mask_Help"] = mask_detection.find("Help").text
            mask_param = mask_detection.find("MaskParameter")
            if mask_param is not None:
                mask_param_value = mask_param.find("Value").text
                mask_param = mask_param.attrib
                mask_param = {f"Mask_Parameter_{key}": value for key, value in mask_param.items()}
                mask_param["Mask_Parameter_Value"] = mask_param_value
                temp = temp | mask_param

        if port_detect is not None:
            params = port_detect.findall("P")
            for param in params:
                temp["Port_" + list(param.attrib.values())[0]] = param.text

        if system_ref_detect is not None:
            ref = list(system_ref_detect.attrib.values())[0]
            tree_output = eT.parse(self.__util_find_file(ref + ".xml"))
            temp["children"],temp["child_conns"] = self.__util_parse_tree(tree_output.getroot(),temp["SID"])

        return temp

    def __util_branch_handling(self,branch,temp):
        branch_params = branch.findall("P")
        nested_branch_detect = branch.findall("Branch")
        for branch_param in branch_params:
            if branch_param.attrib['Name'] == "Src" or branch_param.attrib['Name'] == "Dst":
                if "Branch_" + branch_param.attrib['Name'] in temp:
                    if isinstance(temp["Branch_" + branch_param.attrib['Name']], str):
                        temp_var = temp["Branch_" + branch_param.attrib['Name']]
                        temp["Branch_" + branch_param.attrib['Name']] = []
                        temp["Branch_" + branch_param.attrib['Name']].append(temp_var)
                        temp["Branch_" + branch_param.attrib['Name']].append(branch_param.text.split("#")[0])
                    else:
                        temp["Branch_" + branch_param.attrib['Name']].append(branch_param.text.split("#")[0])
                else:
                    temp["Branch_" + branch_param.attrib['Name']] = branch_param.text.split("#")[0]
        if nested_branch_detect:
            for branch in nested_branch_detect:
                temp = self.__util_branch_handling(branch,temp)
        return temp

    def __util_find_conns(self,element):
        line_list = element.findall("Line")
        conn_list = []
        for line in line_list:
            temp = {}
            params = line.findall("P")
            branches = line.findall("Branch")
            for param in params:
                if param.attrib['Name'] == "Src" or param.attrib['Name'] == "Dst":
                    temp[param.attrib['Name']] = param.text.split("#")[0]

            if branches:
                for branch in branches:
                    temp = self.__util_branch_handling(branch,temp)

            conn_list.append(temp)
        return conn_list

    def __util_find_file(self, target_file_name:str):
        for root, dirs, files in os.walk(self.tempFolderPath):
            if target_file_name in files:
                return os.path.join(root, target_file_name)
        return None

class Grapher:
    def __init__(self,block_list,connections,model_name="root"):
        self.blocks = block_list
        self.conns = connections
        self.visualize(self.conns, model_name)

    @staticmethod
    def find_block(input_block_list, prop, value):
        if input_block_list:
            for block in input_block_list:
                if prop in block and block[prop] == value:
                    return block
                if "children" in block.keys():
                    result = Grapher.find_block(block["children"], prop, value)
                    if result:
                        return result
        return None

    @staticmethod
    def __util_set_node(graph, block):
        if block["BlockType"] == "Inport" or block["BlockType"] == "Outport":
            node_temp = pydot.Node(name=block["Name"], label=block["Name"], shape="box", style='rounded',
                                   tooltip=Grapher.__get_block_val(block))
            graph.add_node(node_temp)
        elif block["BlockType"] == "SubSystem":
            temp_node = graph.get_node(block["Name"])
            if len(temp_node) == 0:
                temp_node = pydot.Node(name=block["Name"], label=block["Name"], shape="box", URL=block["Name"] + ".svg",
                                       tooltip=Grapher.__get_block_val(block))
                graph.add_node(temp_node)
                Grapher(block["children"], block["child_conns"], block["Name"])
            else:
                data = temp_node[0].obj_dict["attributes"]["tooltip"]
                data2 = Grapher.__get_block_val(block)
                existing = Grapher.__parse_block_val_multiline(data)
                this_instance = Grapher.__parse_block_val_multiline(data2)

                if data != data2:
                    block["Name"] = block["Name"] + "_temp"
                    temp_node = pydot.Node(name=block["Name"], label=block["Name"], shape="box",
                                           tooltip=Grapher.__get_block_val(block))
                    graph.add_node(temp_node)
                    Grapher(block["children"], block["child_conns"], block["Name"])

        elif block["BlockType"] == "Logic" or block["BlockType"] == "RelationalOperator":
            if "Operator" not in block.keys():
                node_temp = pydot.Node(name=block["Name"], label=block["Name"], shape="box",
                                       tooltip=Grapher.__get_block_val(block))
                graph.add_node(node_temp)
            else:
                node_temp = pydot.Node(name=block["Name"], label=block["Operator"], shape="box",
                                       tooltip=Grapher.__get_block_val(block))
                graph.add_node(node_temp)
        elif block["BlockType"] == "Constant":
            node_temp = pydot.Node(name=block["Name"], label=block["Value"], shape="box",
                                   tooltip=Grapher.__get_block_val(block))
            graph.add_node(node_temp)
        elif block["BlockType"] == "If":
            node_temp = pydot.Node(name=block["Name"], label=block["BlockType"] + "\n" + block["IfExpression"],
                                   shape="box", tooltip=Grapher.__get_block_val(block))
            graph.add_node(node_temp)
        else:
            node_temp = pydot.Node(name=block["Name"], label=block["Name"], shape="box",
                                   tooltip=Grapher.__get_block_val(block))
            graph.add_node(node_temp)

    @staticmethod
    def __get_block_val(block):
        excluded_keys = {'children', 'child_conns'}
        # formatted_text = '\n'.join(f"{k}: {v}" for d in block for k, v in d.items())
        # formatted_text = '\n'.join(f"{k}: {v}" for k, v in block.items())
        formatted_text = '\n'.join(f"{k}: {v}" for k, v in block.items() if k not in excluded_keys)
        return formatted_text

    def visualize(self, connections:list, name:str):
        graph = pydot.Dot(graph_type='digraph', rankdir='LR')
        for connection in connections:
            src_blk_sid = connection["Src"]
            if "Dst" in connection.keys():
                dst_blks = connection["Dst"]
            else:
                dst_blks = connection["Branch_Dst"]
            src_block = Grapher.find_block(self.blocks, "SID", src_blk_sid)
            Grapher.__util_set_node(graph, src_block)
            if isinstance(dst_blks, list):
                for dst_blk_sid in dst_blks:
                    dst_block = Grapher.find_block(self.blocks, "SID", dst_blk_sid)
                    Grapher.__util_set_node(graph, dst_block)
                    #dot.node(dst_block["Name"], dst_block["Name"], shape='box')
                    temp_edge = pydot.Edge(src_block["Name"],dst_block["Name"],tailport='e', headport='w')
                    graph.add_edge(temp_edge)
            elif isinstance(dst_blks, str):
                dst_block = Grapher.find_block(self.blocks, "SID", dst_blks)
                Grapher.__util_set_node(graph, dst_block)
                temp_edge = pydot.Edge(src_block["Name"],dst_block["Name"],tailport='e', headport='w')
                graph.add_edge(temp_edge)
        dot_string = graph.to_string()
        src = Source(dot_string)
        src.render(os.path.join(os.getcwd(),"output",name), format="svg", cleanup=True)

    @staticmethod
    def __parse_block_val_multiline(formatted_text):
        block = {}
        current_key = None
        current_value_lines = []

        for line in formatted_text.split('\n'):
            if ': ' in line and (line.index(': ') == line.find(': ')):  # key line at start
                if current_key is not None:
                    # save previous key-value pair
                    block[current_key] = '\n'.join(current_value_lines)
                # start new key-value pair
                current_key, value_start = line.split(': ', 1)
                current_value_lines = [value_start]
            else:
                # line is part of the current value (multi-line)
                current_value_lines.append(line)

        # save last key-value pair
        if current_key is not None:
            block[current_key] = '\n'.join(current_value_lines)

        return block

import threading

import mysql.connector
import mysql.connector.pooling

try:
    from VirtualReal702.config import DB_CONFIG
except ImportError:  # pragma: no cover - local direct run fallback
    from config import DB_CONFIG

CREATE_TABLE_SQL_LIST = [
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_console_log (
        pid BIGINT NOT NULL COMMENT '工程ID',
        `time` BIGINT NOT NULL COMMENT '日志时间戳(毫秒)',
        log_text VARCHAR(2048) NOT NULL COMMENT 'HTML格式日志内容',
        KEY idx_pid_time (pid, `time`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='控制台日志表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_node (
        nid VARCHAR(100) NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        fid BIGINT NOT NULL COMMENT '文件ID',
        ics INT NOT NULL COMMENT '输入坐标系',
        ocs INT NOT NULL COMMENT '输出坐标系',
        x DOUBLE NOT NULL COMMENT 'X坐标值',
        y DOUBLE NOT NULL COMMENT 'Y坐标值',
        z DOUBLE NOT NULL COMMENT 'Z坐标值',
        PRIMARY KEY (nid, pid, fid)
    ) COMMENT='模态试验测点坐标表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_element (
        element_no INT NOT NULL COMMENT '单元编号',
        pid BIGINT NOT NULL COMMENT '工程ID',
        element_type VARCHAR(50) NOT NULL COMMENT '单元类型',
        point1 INT NULL COMMENT '节点1编号',
        point2 INT NULL COMMENT '节点2编号',
        point3 INT NULL COMMENT '节点3编号',
        point4 INT NULL COMMENT '节点4编号',
        PRIMARY KEY (element_no, pid)
    ) COMMENT='模态试验测试单元信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_frequency (
        mode_no INT NOT NULL COMMENT '振型编号',
        pid BIGINT NOT NULL COMMENT '工程ID',
        fid BIGINT NOT NULL COMMENT '文件ID',
        frequency DOUBLE NOT NULL COMMENT '频率值',
        damping DOUBLE NOT NULL COMMENT '阻尼比',
        eigenvalue_Re DOUBLE COMMENT '特征值实部',
        eigenvalue_Im DOUBLE COMMENT '特征值虚部',
        PRIMARY KEY (mode_no, pid, fid)
    ) COMMENT='模态试验频率数据表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape (
        mode_no INT NOT NULL COMMENT '振型编号',
        pid BIGINT NOT NULL COMMENT '工程ID',
        modal_shape JSON NOT NULL COMMENT '振型数据',
        PRIMARY KEY (mode_no, pid)
    ) COMMENT='试验振动模态数据表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape_imag (
        mode_no INT NOT NULL COMMENT '振型编号',
        pid BIGINT NOT NULL COMMENT '工程ID',
        point INT NOT NULL COMMENT '节点ID',
        re_ux DOUBLE COMMENT 'x方向实部',
        im_ux DOUBLE COMMENT 'x方向虚部',
        re_uy DOUBLE COMMENT 'y方向实部',
        im_uy DOUBLE COMMENT 'y方向虚部',
        re_uz DOUBLE COMMENT 'z方向实部',
        im_uz DOUBLE COMMENT 'z方向虚部',
        PRIMARY KEY (mode_no, pid, point)
    ) COMMENT='试验振动模态数据表--复模态节点振型'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape_real (
        mode_no INT NOT NULL COMMENT '振型编号',
        pid BIGINT NOT NULL COMMENT '工程ID',
        point INT NOT NULL COMMENT '节点ID',
        ux DOUBLE COMMENT 'x方向位移分量',
        uy DOUBLE COMMENT 'y方向位移分量',
        uz DOUBLE COMMENT 'z方向位移分量',
        PRIMARY KEY (mode_no, pid, point)
    ) COMMENT='试验振动模态数据表--复模态节点振型'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_static_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        fid BIGINT NOT NULL COMMENT '文件ID',
        load_case_no INT NOT NULL COMMENT '载荷工况号',
        result_no INT NOT NULL COMMENT '结果序号',
        point INT NOT NULL COMMENT '测点ID',
        ux DOUBLE NULL COMMENT 'X方向位移',
        uy DOUBLE NULL COMMENT 'Y方向位移',
        uz DOUBLE NULL COMMENT 'Z方向位移',
        rx DOUBLE NULL COMMENT 'X方向转角',
        ry DOUBLE NULL COMMENT 'Y方向转角',
        rz DOUBLE NULL COMMENT 'Z方向转角',
        load_factor DOUBLE NULL COMMENT '载荷因子',
        extra_json JSON NULL COMMENT '扩展信息',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_fid_case_result_point (pid, fid, load_case_no, result_no, point),
        KEY idx_pid_case_result (pid, load_case_no, result_no),
        KEY idx_pid_point (pid, point)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='试验静力结果表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_coord (
        id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        coord_no INT NOT NULL COMMENT '坐标编号',
        ref_coord_no INT NOT NULL COMMENT '参考坐标编号',
        PRIMARY KEY (id, pid)
    ) COMMENT='测试坐标信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_coord (
        id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        fid BIGINT NOT NULL COMMENT '文件ID',
        coord_no INT NOT NULL COMMENT '坐标编号',
        ref_coord_no INT NOT NULL COMMENT '参考坐标编号',
        coord_type VARCHAR(32) NOT NULL COMMENT '坐标类型',
        x1 DOUBLE NOT NULL COMMENT '坐标分量X1',
        x2 DOUBLE NOT NULL COMMENT '坐标分量X2',
        x3 DOUBLE NOT NULL COMMENT '坐标分量X3',
        x4 DOUBLE NOT NULL COMMENT '坐标分量X4',
        x5 DOUBLE NOT NULL COMMENT '坐标分量X5',
        x6 DOUBLE NOT NULL COMMENT '坐标分量X6',
        x7 DOUBLE NOT NULL COMMENT '坐标分量X7',
        x8 DOUBLE NOT NULL COMMENT '坐标分量X8',
        x9 DOUBLE NOT NULL COMMENT '坐标分量X9',
        PRIMARY KEY (id, pid, fid)
    ) COMMENT='有限元坐标信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_material_overview (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        Type VARCHAR(32) NOT NULL COMMENT '材料名称',
        PRIMARY KEY (Id, pid)
    ) COMMENT='有限元模型材料总览表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_isotropic (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        RHO DOUBLE NOT NULL COMMENT '密度',
        E DOUBLE NOT NULL COMMENT '弹性模量',
        NU DOUBLE NOT NULL COMMENT '泊松比',
        GE DOUBLE NOT NULL COMMENT '材料阻尼',
        PRIMARY KEY (Id, pid)
    ) COMMENT='有限元模型各向同性材料表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_ortho2d (
        Id INT NOT NULL COMMENT 'primary key',
        pid BIGINT NOT NULL COMMENT 'project id',
        RHO DOUBLE NULL COMMENT '密度',
        EX DOUBLE NULL COMMENT '弹性模量x方向',
        EY DOUBLE NULL COMMENT '弹性模量y方向',
        GXY DOUBLE NULL COMMENT '剪切模量XY',
        NUXY DOUBLE NULL COMMENT '泊松比XY',
        GXZ DOUBLE NULL COMMENT '剪切模量XZ',
        GYZ DOUBLE NULL COMMENT '剪切模量YZ',
        GE DOUBLE NULL COMMENT '材料阻尼',
        PRIMARY KEY (Id, pid)
    ) COMMENT='FEM orthotropic 2D materials'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_aniso3d (
        Id INT NOT NULL COMMENT 'primary key',
        pid BIGINT NOT NULL COMMENT 'project id',
        RHO DOUBLE NULL COMMENT '密度',
        D11 DOUBLE NULL COMMENT 'D11',
        D12 DOUBLE NULL COMMENT 'D12',
        D13 DOUBLE NULL COMMENT 'D13',
        D14 DOUBLE NULL COMMENT 'D14',
        D15 DOUBLE NULL COMMENT 'D15',
        D16 DOUBLE NULL COMMENT 'D16',
        D22 DOUBLE NULL COMMENT 'D22',
        D23 DOUBLE NULL COMMENT 'D23',
        D24 DOUBLE NULL COMMENT 'D24',
        D25 DOUBLE NULL COMMENT 'D25',
        D26 DOUBLE NULL COMMENT 'D26',
        D33 DOUBLE NULL COMMENT 'D33',
        D34 DOUBLE NULL COMMENT 'D34',
        D35 DOUBLE NULL COMMENT 'D35',
        D36 DOUBLE NULL COMMENT 'D36',
        D44 DOUBLE NULL COMMENT 'D44',
        D45 DOUBLE NULL COMMENT 'D45',
        D46 DOUBLE NULL COMMENT 'D46',
        D55 DOUBLE NULL COMMENT 'D55',
        D56 DOUBLE NULL COMMENT 'D56',
        D66 DOUBLE NULL COMMENT 'D66',
        GE DOUBLE NULL COMMENT '材料阻尼',
        PRIMARY KEY (Id, pid)
    ) COMMENT='FEM anisotropic 3D materials'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_property (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        Type VARCHAR(32) NOT NULL COMMENT '属性名称',
        PRIMARY KEY (Id, pid)
    ) COMMENT='有限元模型属性表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_shell_property (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        Thickness DOUBLE NOT NULL COMMENT '厚度',
        NSM DOUBLE NOT NULL COMMENT '非结构质量',
        THETA DOUBLE NOT NULL COMMENT '旋转角',
        element_set VARCHAR(255) NULL COMMENT '单元集名称',
        PRIMARY KEY (Id, pid)
    ) COMMENT='有限元模型壳单元属性表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_beam_property (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        AX DOUBLE NOT NULL COMMENT '截面积',
        AY DOUBLE NOT NULL COMMENT 'Y向剪切变形缩减截面',
        AZ DOUBLE NOT NULL COMMENT 'Z向剪切变形缩减截面',
        IX DOUBLE NOT NULL COMMENT '抗扭惯性矩',
        IY DOUBLE NOT NULL COMMENT 'Y轴惯性矩',
        IZ DOUBLE NOT NULL COMMENT 'Z轴惯性矩',
        CW DOUBLE NOT NULL COMMENT '翘曲系数',
        YN DOUBLE NOT NULL COMMENT '中性轴Y向坐标',
        ZN DOUBLE NOT NULL COMMENT '中性轴Z向坐标',
        NSM DOUBLE NOT NULL COMMENT '非结构质量',
        PRIMARY KEY (Id, pid)
    ) COMMENT='有限元模型梁单元属性表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_solid_property (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        MID INT NULL COMMENT '材料ID',
        CID INT NULL COMMENT '坐标系ID',
        PRIMARY KEY (Id, pid)
    ) COMMENT='FEM solid properties'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_layered_property (
        Id INT NOT NULL COMMENT 'primary key',
        pid BIGINT NOT NULL COMMENT 'project id',
        Offset_L DOUBLE NULL COMMENT 'Offset',
        Theta DOUBLE NULL COMMENT 'Theta',
        GE DOUBLE NULL COMMENT 'GE',
        NSM DOUBLE NULL COMMENT 'NSM',
        Layers INT NULL COMMENT 'Layers',
        PRIMARY KEY (Id, pid)
    ) COMMENT='FEM layered properties'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_boundary (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        Node BIGINT NOT NULL COMMENT '节点号',
        UX DOUBLE COMMENT 'X方向位移约束, 指定位移',
        UY DOUBLE COMMENT 'Y方向位移约束, 指定位移',
        UZ DOUBLE COMMENT 'Z方向位移约束, 指定位移',
        RX DOUBLE COMMENT 'X方向旋转约束, 指定位移',
        RY DOUBLE COMMENT 'Y方向旋转约束, 指定位移',
        RZ DOUBLE COMMENT 'Z方向旋转约束, 指定位移',
        PRIMARY KEY (Id, pid)
    ) COMMENT='模型约束条件'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_pairs (
        pid INT NOT NULL COMMENT '工程ID',
        node INT NOT NULL COMMENT '节点编号',
        point INT NOT NULL COMMENT '点编号',
        distance FLOAT NOT NULL COMMENT '两点距离',
        x_offset FLOAT NOT NULL COMMENT 'X方向偏移量',
        y_offset FLOAT NOT NULL COMMENT 'Y方向偏移量',
        z_offset FLOAT NOT NULL COMMENT 'Z方向偏移量',
        PRIMARY KEY (pid, node, point)
    ) COMMENT='有限元节点配对信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_transform_operation (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        transform_type VARCHAR(32) NOT NULL COMMENT '变换类型（有限元/试验）',
        matrix4_json JSON NOT NULL COMMENT '4x4矩阵数据',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_transform_type (pid, transform_type),
        KEY idx_pid_updated_at (pid, updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='空间匹配变换记录表';
    """,
    """
    -- project configuration table
    -- test_model_x/y/z: test model size
    -- fem_model_x/y/z: fem model size
    -- coefficients_json: coefficient map
    -- extra_json: extra config payload
    CREATE TABLE IF NOT EXISTS t_mt_py_project_config (
        pid BIGINT NOT NULL COMMENT 'project id',
        test_model_x DOUBLE NULL COMMENT '试验模型x方向尺寸',
        test_model_y DOUBLE NULL COMMENT '试验模型y方向尺寸',
        test_model_z DOUBLE NULL COMMENT '试验模型z方向尺寸',
        fem_model_x DOUBLE NULL COMMENT '有限元模型x方向尺寸',
        fem_model_y DOUBLE NULL COMMENT '有限元模型y方向尺寸',
        fem_model_z DOUBLE NULL COMMENT '有限元模型z方向尺寸',
        coefficients_json JSON NULL COMMENT '系数对应表',
        extra_json JSON NULL COMMENT '额外系数对应表',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'created time',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'updated time',
        PRIMARY KEY (pid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='project configuration';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_dof_pairs (
        pid INT NOT NULL COMMENT '工程ID',
        node INT NOT NULL COMMENT '节点编号',
        point INT NOT NULL COMMENT '点编号',
        dof VARCHAR(32) NOT NULL COMMENT '自由度类型',
        cx FLOAT NOT NULL COMMENT 'X方向分量',
        cy FLOAT NOT NULL COMMENT 'Y方向分量',
        cz FLOAT NOT NULL COMMENT 'Z方向分量',
        PRIMARY KEY (pid, node, point)
    ) COMMENT='有限元自由度配对信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_shape_pairs (
        pid INT NOT NULL COMMENT '工程ID',
        fem_res VARCHAR(32) NOT NULL COMMENT '有限元计算结果',
        test_res VARCHAR(32) NOT NULL COMMENT '试验测试结果',
        DAC FLOAT NOT NULL COMMENT 'DAC(%)',
        DSF FLOAT NOT NULL COMMENT 'DSF',
        PRIMARY KEY (pid)
    ) COMMENT='有限元静力振型配对表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_supported_quantity (
        quantity_code VARCHAR(32) NOT NULL COMMENT '修正量编码',
        quantity_name VARCHAR(200) NOT NULL COMMENT '修正量名称',
        unit VARCHAR(50) NULL COMMENT '单位',
        enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
        sort_no INT NOT NULL DEFAULT 0 COMMENT '排序号',
        PRIMARY KEY (quantity_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='支持的修正量表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_quantity_set_capability (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        quantity_code VARCHAR(32) NOT NULL COMMENT '修正量编码',
        set_name VARCHAR(200) NOT NULL COMMENT '集合名称',
        set_type VARCHAR(32) NOT NULL COMMENT '集合类型',
        set_scope VARCHAR(32) NOT NULL COMMENT '集合范围',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        part_name VARCHAR(200) NULL COMMENT '零件名称',
        set_role VARCHAR(64) NOT NULL COMMENT '集合角色',
        element_family VARCHAR(32) NULL COMMENT '单元族',
        section_type VARCHAR(64) NULL COMMENT '截面类型',
        material_name VARCHAR(200) NULL COMMENT '材料名称',
        member_count INT NOT NULL DEFAULT 0 COMMENT '成员数量',
        supports_global TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否支持全局参数',
        supports_local TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否支持局部参数',
        current_value DOUBLE NULL COMMENT '共享当前值',
        extra_json JSON NULL COMMENT '扩展信息',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_quantity_set_capability (pid, quantity_code, set_name, set_type, set_scope, instance_name, part_name),
        KEY idx_pid_quantity_code (pid, quantity_code),
        KEY idx_pid_set_name (pid, set_name, set_type, set_scope)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='修正量与集合能力目录表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_selected_parameter (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        parameter_group_name VARCHAR(200) NOT NULL COMMENT '参数组名称',
        parameter_name VARCHAR(200) NOT NULL COMMENT '参数名称',
        quantity_code VARCHAR(32) NOT NULL COMMENT '修正量编码',
        selection_mode VARCHAR(32) NOT NULL COMMENT '选择模式',
        set_name VARCHAR(200) NOT NULL COMMENT '集合名称',
        set_type VARCHAR(32) NOT NULL COMMENT '集合类型',
        set_scope VARCHAR(32) NOT NULL COMMENT '集合范围',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        part_name VARCHAR(200) NULL COMMENT '零件名称',
        element_label BIGINT NULL COMMENT '单元标签',
        current_value DOUBLE NULL COMMENT '当前值',
        lower DOUBLE NOT NULL DEFAULT 0 COMMENT '下界',
        upper DOUBLE NOT NULL DEFAULT 0 COMMENT '上界',
        prob_id BIGINT NOT NULL DEFAULT 0 COMMENT '问题ID',
        scatter FLOAT NOT NULL DEFAULT 0.25 COMMENT '离散度',
        description VARCHAR(255) NOT NULL DEFAULT '' COMMENT '描述',
        extra_json JSON NULL COMMENT '扩展信息',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_parameter_name (pid, parameter_name),
        KEY idx_pid_group_name (pid, parameter_group_name),
        KEY idx_pid_quantity_mode (pid, quantity_code, selection_mode)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='已选修正参数表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_sol200_parameter_config (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        parameter_no INT NOT NULL COMMENT '参数序号',
        parameter_name VARCHAR(200) NOT NULL COMMENT '参数名称',
        parameter_type VARCHAR(32) NOT NULL COMMENT '参数类型',
        property_id BIGINT NULL COMMENT '属性ID',
        material_id BIGINT NULL COMMENT '材料ID',
        element_id BIGINT NULL COMMENT '单元ID',
        initial_value DOUBLE NOT NULL COMMENT '初始值',
        lower_bound DOUBLE NULL COMMENT '下界',
        upper_bound DOUBLE NULL COMMENT '上界',
        extra_json JSON NULL COMMENT '扩展信息',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_sol200_parameter_name (pid, parameter_name),
        UNIQUE KEY uk_pid_sol200_parameter_no (pid, parameter_no),
        KEY idx_pid_sol200_parameter_type (pid, parameter_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SOL200 参数配置表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_sol200_response_config (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        response_no INT NOT NULL COMMENT '响应序号',
        response_name VARCHAR(200) NOT NULL COMMENT '响应名称',
        response_type VARCHAR(32) NOT NULL COMMENT '响应类型',
        mode_number INT NULL COMMENT '模态阶次',
        extra_json JSON NULL COMMENT '扩展信息',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_sol200_response_name (pid, response_name),
        UNIQUE KEY uk_pid_sol200_response_no (pid, response_no),
        KEY idx_pid_sol200_response_type (pid, response_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SOL200 响应配置表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_definition (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        parameter_name VARCHAR(200) NOT NULL COMMENT '参数名称',
        expression VARCHAR(500) NULL COMMENT '参数表达式',
        scalar_value DOUBLE NULL COMMENT '参数标量值',
        is_design_parameter TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否为设计参数',
        design_order INT NULL COMMENT '设计参数顺序',
        extra_json JSON NULL COMMENT '扩展信息',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_parameter_definition (pid, parameter_name),
        KEY idx_pid_design_parameter (pid, is_design_parameter, design_order)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='输入文件解析参数定义表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_target (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        parameter_name VARCHAR(200) NOT NULL COMMENT '参数名称',
        target_type VARCHAR(32) NOT NULL COMMENT '目标类型',
        set_name VARCHAR(200) NOT NULL COMMENT '集合名称',
        set_type VARCHAR(32) NOT NULL COMMENT '集合类型',
        set_scope VARCHAR(32) NOT NULL COMMENT '集合范围',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        part_name VARCHAR(200) NULL COMMENT '零件名称',
        source_keyword VARCHAR(100) NOT NULL COMMENT '来源关键字',
        source_path VARCHAR(255) NOT NULL COMMENT '来源路径',
        component_name VARCHAR(100) NULL COMMENT '分量名称',
        extra_json JSON NULL COMMENT '扩展信息',
        PRIMARY KEY (id),
        KEY idx_pid_parameter_target (pid, parameter_name),
        KEY idx_pid_target_set (pid, set_name, set_type, set_scope)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='输入文件解析参数目标映射表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_design_response_catalog (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        response_no INT NOT NULL COMMENT '响应序号',
        request_no INT NOT NULL COMMENT '请求序号',
        step_name VARCHAR(200) NULL COMMENT '分析步名称',
        frequency INT NOT NULL DEFAULT 1 COMMENT '响应频次',
        region_type VARCHAR(32) NOT NULL COMMENT '区域类型',
        set_name VARCHAR(200) NOT NULL COMMENT '集合名称',
        variables_json JSON NULL COMMENT '变量信息',
        extra_json JSON NULL COMMENT '扩展信息',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_design_response (pid, response_no, request_no),
        KEY idx_pid_design_response_step (pid, step_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='输入文件解析设计响应目录表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_octree_cache (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '项目ID',
        source_file_path VARCHAR(500) NOT NULL COMMENT '源文件路径',
        cache_file_path VARCHAR(500) NOT NULL COMMENT '缓存文件路径',
        node_count INT NOT NULL DEFAULT 0 COMMENT '节点数量',
        instance_count INT NOT NULL DEFAULT 0 COMMENT '实例数量',
        bbox_min VARCHAR(255) NULL COMMENT '整体包围盒最小值',
        bbox_max VARCHAR(255) NULL COMMENT '整体包围盒最大值',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_source_file (pid, source_file_path(255))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='节点八叉树缓存元数据';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_match (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '项目ID',
        test_node_id VARCHAR(100) NOT NULL COMMENT '试验测点编号',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        fem_node_label BIGINT NOT NULL COMMENT '有限元节点号',
        distance DOUBLE NOT NULL COMMENT '距离',
        x_offset DOUBLE NOT NULL COMMENT 'X偏移',
        y_offset DOUBLE NOT NULL COMMENT 'Y偏移',
        z_offset DOUBLE NOT NULL COMMENT 'Z偏移',
        transform_json JSON NULL COMMENT '匹配时使用的变换',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_test_node (pid, test_node_id),
        KEY idx_pid_instance (pid, instance_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='试验测点到有限元节点匹配结果';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_dof_match (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        test_node_id VARCHAR(100) NOT NULL COMMENT '试验测点编号',
        test_dof VARCHAR(32) NOT NULL COMMENT '试验自由度',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        part_name VARCHAR(200) NULL COMMENT '零件名称',
        fem_node_label BIGINT NOT NULL COMMENT '有限元节点号',
        fem_dof VARCHAR(32) NOT NULL COMMENT '有限元自由度',
        direction_x DOUBLE NOT NULL COMMENT '方向X分量',
        direction_y DOUBLE NOT NULL COMMENT '方向Y分量',
        direction_z DOUBLE NOT NULL COMMENT '方向Z分量',
        match_score DOUBLE NOT NULL DEFAULT 0 COMMENT '匹配得分',
        transform_json JSON NULL COMMENT '变换信息',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_test_dof (pid, test_node_id, test_dof),
        KEY idx_pid_fem_node (pid, instance_name, fem_node_label),
        KEY idx_pid_fem_dof (pid, fem_dof)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元自由度匹配结果表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_catalog (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        response_code VARCHAR(200) NOT NULL COMMENT '响应编码',
        response_name VARCHAR(255) NOT NULL COMMENT '响应名称',
        response_type VARCHAR(64) NOT NULL COMMENT '响应类型',
        entity_type VARCHAR(64) NOT NULL COMMENT '实体类型',
        test_mode_no INT NULL COMMENT '试验振型号',
        test_node_id VARCHAR(100) NULL COMMENT '试验测点编号',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        part_name VARCHAR(200) NULL COMMENT '零件名称',
        fem_node_label BIGINT NULL COMMENT '有限元节点号',
        component VARCHAR(32) NULL COMMENT '响应分量',
        unit VARCHAR(50) NULL COMMENT '单位',
        seq_no INT NULL COMMENT '显示顺序',
        source_table VARCHAR(100) NULL COMMENT '来源数据表',
        extra_json JSON NULL COMMENT '扩展信息',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_response_code (pid, response_code),
        KEY idx_pid_response_type (pid, response_type),
        KEY idx_pid_seq (pid, seq_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元响应目录表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_modal_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        mode_no INT NOT NULL COMMENT '有限元振型号',
        frequency DOUBLE NULL COMMENT '频率值',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        part_name VARCHAR(200) NULL COMMENT '零件名称',
        fem_node_label BIGINT NOT NULL COMMENT '有限元节点号',
        u1 DOUBLE NULL COMMENT 'X向位移U1',
        u2 DOUBLE NULL COMMENT 'Y向位移U2',
        u3 DOUBLE NULL COMMENT 'Z向位移U3',
        extra_json JSON NULL COMMENT '扩展信息',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_mode_node (pid, mode_no, instance_name, fem_node_label),
        KEY idx_pid_mode (pid, mode_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元模态结果表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        load_case_no INT NOT NULL DEFAULT 1 COMMENT '载荷工况号',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        part_name VARCHAR(200) NULL COMMENT '零件名称',
        fem_node_label BIGINT NOT NULL COMMENT '有限元节点号',
        u1 DOUBLE NULL COMMENT 'X向位移U1',
        u2 DOUBLE NULL COMMENT 'Y向位移U2',
        u3 DOUBLE NULL COMMENT 'Z向位移U3',
        ur1 DOUBLE NULL COMMENT 'X向转角UR1',
        ur2 DOUBLE NULL COMMENT 'Y向转角UR2',
        ur3 DOUBLE NULL COMMENT 'Z向转角UR3',
        extra_json JSON NULL COMMENT '扩展信息',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_static_case_node (pid, load_case_no, instance_name, fem_node_label),
        KEY idx_pid_static_case (pid, load_case_no),
        KEY idx_pid_static_node (pid, fem_node_label)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元静力结果表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_modal_correlation (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        test_mode_no INT NOT NULL COMMENT '试验振型号',
        fem_mode_no INT NOT NULL COMMENT '有限元振型号',
        dof_pair_count INT NOT NULL DEFAULT 0 COMMENT '自由度配对数量',
        dac DOUBLE NOT NULL COMMENT 'DAC百分比',
        dsf DOUBLE NOT NULL COMMENT 'DSF值',
        mac DOUBLE NOT NULL COMMENT 'MAC值',
        freq_test DOUBLE NULL COMMENT '试验频率',
        freq_fem DOUBLE NULL COMMENT '有限元频率',
        freq_error_ratio DOUBLE NULL COMMENT '频率误差比',
        flip BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否需要相位翻转',
        extra_json JSON NULL COMMENT '扩展信息',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_mode_pair (pid, test_mode_no, fem_mode_no),
        KEY idx_pid_test_mode (pid, test_mode_no),
        KEY idx_pid_fem_mode (pid, fem_mode_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元模态相关性表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_analysis_run (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        project_id BIGINT NULL COMMENT '项目ID',
        case_name VARCHAR(200) NULL COMMENT '工况名称',
        run_no VARCHAR(100) NULL COMMENT '分析批次号',
        source_kind VARCHAR(32) NULL COMMENT '灵敏度结果来源类型',
        op2_path VARCHAR(1024) NULL COMMENT 'OP2结果文件路径',
        matrix_path VARCHAR(1024) NULL COMMENT '矩阵结果文件路径',
        bdf_path VARCHAR(1024) NULL COMMENT 'BDF文件路径',
        metadata_path VARCHAR(1024) NULL COMMENT '元数据文件路径',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        KEY idx_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元分析任务表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_def (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        project_id BIGINT NULL COMMENT '项目ID',
        analysis_run_id BIGINT NOT NULL COMMENT '分析任务ID',
        response_code VARCHAR(100) NOT NULL COMMENT '响应编码',
        response_name VARCHAR(200) NOT NULL COMMENT '响应名称',
        response_type VARCHAR(32) NULL COMMENT '响应类型',
        mode_number INT NULL COMMENT '模态阶次',
        unit VARCHAR(50) NULL COMMENT '单位',
        seq_no INT NULL COMMENT '显示顺序',
        PRIMARY KEY (id),
        UNIQUE KEY uk_run_response_code (analysis_run_id, response_code),
        KEY idx_run_seq (analysis_run_id, seq_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元响应定义表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_def (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        project_id BIGINT NULL COMMENT '项目ID',
        analysis_run_id BIGINT NOT NULL COMMENT '分析任务ID',
        param_code VARCHAR(100) NOT NULL COMMENT '参数编码',
        param_name VARCHAR(200) NOT NULL COMMENT '参数名称',
        param_type VARCHAR(32) NULL COMMENT '参数类型',
        material_id BIGINT NULL COMMENT '材料ID',
        property_id BIGINT NULL COMMENT '属性ID',
        element_id BIGINT NULL COMMENT '单元ID',
        source_material_id BIGINT NULL COMMENT '源材料ID',
        source_property_id BIGINT NULL COMMENT '源属性ID',
        initial_value DOUBLE NULL COMMENT '初始值',
        lower_bound DOUBLE NULL COMMENT '下界',
        upper_bound DOUBLE NULL COMMENT '上界',
        unit VARCHAR(50) NULL COMMENT '单位',
        seq_no INT NULL COMMENT '显示顺序',
        PRIMARY KEY (id),
        UNIQUE KEY uk_run_param_code (analysis_run_id, param_code),
        KEY idx_run_seq (analysis_run_id, seq_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元参数定义表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_sensitivity_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        project_id BIGINT NULL COMMENT '项目ID',
        analysis_run_id BIGINT NOT NULL COMMENT '分析任务ID',
        parameter_id BIGINT NOT NULL COMMENT '参数ID',
        response_id BIGINT NOT NULL COMMENT '响应ID',
        sensitivity_value DECIMAL(24,12) NOT NULL COMMENT '灵敏度值',
        PRIMARY KEY (id),
        UNIQUE KEY uk_run_param_resp (analysis_run_id, parameter_id, response_id),
        KEY idx_run_param (analysis_run_id, parameter_id),
        KEY idx_run_resp (analysis_run_id, response_id),
        KEY idx_run_resp_value (analysis_run_id, response_id, sensitivity_value)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元灵敏度结果表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_overview (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        response_type BIGINT NOT NULL COMMENT '响应类型',
        scatter FLOAT NOT NULL COMMENT '离散度',
        value FLOAT NOT NULL COMMENT '当前值',
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='响应总览表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_responses (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        response_type VARCHAR(32) NOT NULL COMMENT '响应类型',
        sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型',
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='可选响应表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_displacement_responses (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        data_source VARCHAR(32) NOT NULL COMMENT '数据来源',
        load_case_no VARCHAR(32) NOT NULL COMMENT '载荷工况编号',
        node_label BIGINT NOT NULL COMMENT '节点编号',
        dof VARCHAR(32) NOT NULL COMMENT '自由度',
        scatter FLOAT NOT NULL COMMENT '离散度',
        value FLOAT NOT NULL COMMENT '当前值',
        sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型',
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='位移响应表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_strain_responses (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        data_source VARCHAR(32) NOT NULL COMMENT '数据来源',
        load_case_no VARCHAR(32) NOT NULL COMMENT '载荷工况编号',
        node_label BIGINT NOT NULL COMMENT '节点编号',
        ele_nodes VARCHAR(100) NOT NULL COMMENT '单元节点列表',
        group_type VARCHAR(32) NOT NULL COMMENT '分组',
        direction VARCHAR(32) NOT NULL COMMENT '方向',
        coord VARCHAR(32) NOT NULL COMMENT '坐标系',
        scatter FLOAT NOT NULL COMMENT '离散度',
        value FLOAT NOT NULL COMMENT '当前值',
        sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型',
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='应变响应表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_stress_responses (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        data_source VARCHAR(32) NOT NULL COMMENT '数据来源',
        load_case_no VARCHAR(32) NOT NULL COMMENT '载荷工况编号',
        node_label BIGINT NOT NULL COMMENT '节点编号',
        ele_nodes VARCHAR(100) NOT NULL COMMENT '单元节点列表',
        group_type VARCHAR(32) NOT NULL COMMENT '分组',
        direction VARCHAR(32) NOT NULL COMMENT '方向',
        coord VARCHAR(32) NOT NULL COMMENT '坐标系',
        scatter FLOAT NOT NULL COMMENT '离散度',
        value FLOAT NOT NULL COMMENT '当前值',
        sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型',
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='应力响应表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_relative_error (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        point_name VARCHAR(200) NOT NULL COMMENT '测点名称',
        error_value FLOAT NOT NULL COMMENT '误差值',
        PRIMARY KEY (id),
        KEY idx_pid (pid),
        KEY idx_pid_point_name (pid, point_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='相对误差表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_confidence (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        response_type VARCHAR(32) NOT NULL COMMENT '响应类型',
        confidence_value FLOAT NOT NULL COMMENT '置信度值',
        PRIMARY KEY (id),
        KEY idx_pid (pid),
        KEY idx_pid_response_type (pid, response_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='置信度表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_displacement_scale_factor (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        scale_factor FLOAT NOT NULL COMMENT '缩放因子值',
        PRIMARY KEY (id),
        KEY idx_pid (pid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='位移缩放因子表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_correlation_scatter (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        measure_point_value FLOAT NOT NULL COMMENT '测点值',
        node_value FLOAT NOT NULL COMMENT '节点值',
        measure_name VARCHAR(200) NOT NULL COMMENT '测点名称',
        node_name VARCHAR(200) NOT NULL COMMENT '节点名称',
        PRIMARY KEY (id),
        KEY idx_pid (pid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='相关性散点图表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_analysis_error (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        load_case_no INT NOT NULL DEFAULT 1 COMMENT '载荷工况号',
        result_no INT NOT NULL DEFAULT 1 COMMENT '结果序号',
        point_no VARCHAR(64) NOT NULL COMMENT '测点号',
        node_no VARCHAR(128) NULL COMMENT '节点号',
        component_name VARCHAR(32) NOT NULL COMMENT '分量名称',
        point_value FLOAT COMMENT '测点值',
        initial_node_value FLOAT COMMENT '初始节点值',
        initial_relative_error FLOAT COMMENT '初始相对误差',
        initial_abs_error REAL COMMENT '初始绝对误差',
        updated_node_value FLOAT COMMENT '修正后节点值',
        updated_relative_error FLOAT COMMENT '修正后相对误差',
        updated_abs_error REAL COMMENT '修正后绝对误差',
        sensor_type_id BIGINT COMMENT '传感器类型ID',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_case_result_point_component (pid, load_case_no, result_no, point_no, component_name),
        KEY idx_pid (pid),
        KEY idx_pid_point (pid, point_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='误差分析表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_model_update_static_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        batch_no VARCHAR(32) NOT NULL COMMENT '批次号',
        step_name VARCHAR(200) NULL COMMENT '分析步名称',
        frame_idx INT NOT NULL DEFAULT 0 COMMENT '帧序号',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        part_name VARCHAR(200) NULL COMMENT '零件名称',
        fem_node_label BIGINT NOT NULL COMMENT '有限元节点号',
        u1 DOUBLE NULL COMMENT 'X向位移',
        u2 DOUBLE NULL COMMENT 'Y向位移',
        u3 DOUBLE NULL COMMENT 'Z向位移',
        extra_json JSON NULL COMMENT '扩展信息',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_batch_frame_node (pid, batch_no, frame_idx, instance_name, fem_node_label),
        KEY idx_pid_batch (pid, batch_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型修正最终位移结果表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_manual_response (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        response_type VARCHAR(32) NOT NULL COMMENT '响应类型',
        step_name VARCHAR(100) NOT NULL DEFAULT '' COMMENT '分析步名称',
        dof VARCHAR(32) NOT NULL COMMENT '自由度',
        scatter FLOAT NOT NULL COMMENT '离散度',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_response_step_dof (pid, response_type, step_name, dof),
        KEY idx_pid_response_type (pid, response_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='手工录入响应表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_dac_dsf (
        pid BIGINT NOT NULL COMMENT '工程ID',
        dac FLOAT COMMENT 'DAC指标值',
        dsf FLOAT COMMENT 'DSF指标值',
        PRIMARY KEY (pid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模态相关性指标表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_tracking_value (
        pid BIGINT NOT NULL COMMENT '工程ID',
        batch_no INT NOT NULL DEFAULT 1 COMMENT '批次号',
        tracking_type VARCHAR(10) NOT NULL COMMENT '跟踪类型（参数跟踪/响应跟踪）',
        tracking_name VARCHAR(100) NOT NULL COMMENT '跟踪名称',
        iteration INT NOT NULL COMMENT '迭代步',
        track_value FLOAT COMMENT '跟踪值',
        PRIMARY KEY (pid, batch_no, tracking_type, tracking_name, iteration)
    ) COMMENT='跟踪值表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_tracking_iteration (
        pid BIGINT NOT NULL COMMENT '工程ID',
        batch_no INT NOT NULL DEFAULT 1 COMMENT '批次号',
        iterations INT COMMENT '迭代次数',
        PRIMARY KEY (pid, batch_no)
    ) COMMENT='跟踪迭代表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_bayesian_iteration_metric (
        project_id BIGINT NOT NULL COMMENT 'project id',
        batch_no INT NOT NULL DEFAULT 1 COMMENT 'batch number',
        iteration INT NOT NULL COMMENT 'iteration number',
        ccabs DOUBLE COMMENT 'CCABS convergence metric',
        rel_res DOUBLE COMMENT 'relative residual norm',
        ra_norm DOUBLE COMMENT 'model response norm',
        re_norm DOUBLE COMMENT 'target response norm',
        dr_norm DOUBLE COMMENT 'response residual norm',
        dx_norm DOUBLE COMMENT 'parameter update norm',
        max_abs_dparam DOUBLE COMMENT 'maximum absolute parameter update',
        mean_abs_response_diff DOUBLE COMMENT 'mean absolute response difference percent',
        max_abs_response_diff DOUBLE COMMENT 'maximum absolute response difference percent',
        PRIMARY KEY (project_id, batch_no, iteration)
    ) COMMENT='bayesian iteration metrics';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_relevance_tracking (
        pid BIGINT NOT NULL COMMENT '工程ID',
        iteration INT NOT NULL COMMENT '迭代步',
        type VARCHAR(32) NOT NULL COMMENT '指标类型',
        value FLOAT COMMENT '指标值',
        PRIMARY KEY (pid, iteration, type)
    ) COMMENT='模型修正相关性指标跟踪表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_variation (
        pid BIGINT NOT NULL COMMENT '工程ID',
        batch_no INT NOT NULL DEFAULT 1 COMMENT '批次号',
        parameter_name VARCHAR(100) NOT NULL COMMENT '参数名称',
        parameter_hierarchy VARCHAR(10) COMMENT '参数层级',
        parameter_type VARCHAR(10) COMMENT '参数类型',
        parameter_scope VARCHAR(32) COMMENT '参数范围',
        ori_value FLOAT COMMENT '原始值',
        result_value FLOAT COMMENT '结果值',
        parameter_variation FLOAT COMMENT '参数变化',
        PRIMARY KEY (pid, batch_no, parameter_name)
    ) COMMENT='参数变化表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_difference (
        pid INTEGER NOT NULL COMMENT '工程ID',
        batch_no INT NOT NULL DEFAULT 1 COMMENT '批次号',
        response_name VARCHAR(100) NOT NULL COMMENT '响应名称',
        iteration INT NOT NULL COMMENT '迭代次数',
        cal_result_value FLOAT COMMENT '计算结果值',
        test_result_value FLOAT COMMENT '测试结果值',
        response_diff FLOAT COMMENT '响应差异(%)',
        PRIMARY KEY (pid, batch_no, response_name, iteration)
    ) COMMENT='响应差异表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_measuring_point_info(
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        measuring_point_name VARCHAR(64) NOT NULL COMMENT '测点名称',
        project_id BIGINT NOT NULL COMMENT '项目ID',
        sensor_type_id BIGINT NOT NULL COMMENT '传感器类型ID',
        x_position DOUBLE NOT NULL COMMENT 'X坐标',
        y_position DOUBLE NOT NULL COMMENT 'Y坐标',
        z_position DOUBLE NOT NULL COMMENT 'Z坐标',
        x_angle DOUBLE NOT NULL COMMENT '角度x',
        y_angle DOUBLE NOT NULL COMMENT '角度y',
        z_angle DOUBLE NOT NULL COMMENT '角度z',
        data_source VARCHAR(32) NOT NULL COMMENT '数据来源',
        PRIMARY KEY (id, measuring_point_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='测点信息表'; 
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_channel_info (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        channel_name VARCHAR(64) NOT NULL COMMENT '通道名称',
        measure_point_id BIGINT NOT NULL COMMENT '测点ID',
        project_id BIGINT NOT NULL COMMENT '项目ID',
        direction INT NOT NULL COMMENT '方向:1-x, 2-y, 3-z',
        data_operate CHAR(1) NOT NULL COMMENT '+ - 操作类型',
        PRIMARY KEY (id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通道信息表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_background_task (
        task_id VARCHAR(64) NOT NULL COMMENT '任务ID',
        task_type VARCHAR(128) NOT NULL COMMENT '任务类型',
        interface_code VARCHAR(128) NULL COMMENT '接口编码',
        task_kind VARCHAR(32) NOT NULL DEFAULT 'internal' COMMENT '任务分类',
        project_id BIGINT NULL COMMENT '项目ID',
        status VARCHAR(32) NOT NULL COMMENT '任务状态',
        execute_count INT NOT NULL DEFAULT 0 COMMENT '已执行次数',
        max_execute_count INT NOT NULL DEFAULT 3 COMMENT '最大执行次数',
        handler_module VARCHAR(255) NULL COMMENT '处理函数模块',
        handler_name VARCHAR(128) NULL COMMENT '处理函数名称',
        pass_task_id TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否传递task_id',
        submitted_at VARCHAR(64) NULL COMMENT '提交时间',
        started_at VARCHAR(64) NULL COMMENT '开始时间',
        finished_at VARCHAR(64) NULL COMMENT '结束时间',
        request_json LONGTEXT NULL COMMENT '请求JSON',
        kwargs_json LONGTEXT NULL COMMENT '执行参数JSON',
        progress_json LONGTEXT NULL COMMENT '进度JSON',
        result_json LONGTEXT NULL COMMENT '结果JSON',
        error_json LONGTEXT NULL COMMENT '错误JSON',
        PRIMARY KEY (task_id),
        KEY idx_task_status (status),
        KEY idx_project_interface (project_id, interface_code),
        KEY idx_project_status (project_id, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型修正后台任务表';
    """
]

_tables_ensured = False
_tables_ensure_lock = threading.Lock()
_connection_pool = None
_connection_pool_lock = threading.Lock()


def _connection_kwargs():
    return {
        "host": DB_CONFIG["host"],
        "port": DB_CONFIG["port"],
        "user": DB_CONFIG["user"],
        "password": DB_CONFIG["password"],
        "database": DB_CONFIG["database"],
        "charset": DB_CONFIG["charset"],
        "use_pure": True,
    }


def _get_connection_pool():
    global _connection_pool
    if _connection_pool is not None:
        return _connection_pool

    _connection_pool_lock.acquire()
    try:
        if _connection_pool is None:
            pool_name = str(DB_CONFIG.get("pool_name", "virtualreal702_pool"))
            pool_size = max(1, int(DB_CONFIG.get("pool_size", 10)))
            pool_reset_session = bool(DB_CONFIG.get("pool_reset_session", True))
            _connection_pool = mysql.connector.pooling.MySQLConnectionPool(
                pool_name=pool_name,
                pool_size=pool_size,
                pool_reset_session=pool_reset_session,
                **_connection_kwargs(),
            )
        return _connection_pool
    finally:
        if _connection_pool_lock.locked():
            _connection_pool_lock.release()

def get_connection():
    return _get_connection_pool().get_connection()


def initialize_database_runtime(*, ensure_tables: bool = True, warm_connection: bool = True) -> None:
    """
    Eagerly create the MySQL connection pool during application startup.

    Why this exists:
    - The old lazy-init path built the pool on the first business request.
    - That makes the first few frontend calls look "stuck", even though the
      actual delay is pool creation / initial database handshake.
    - Moving this work to startup makes request latency more stable and also
      fails fast when the database configuration is invalid.
    """
    _get_connection_pool()
    if warm_connection:
        conn = get_connection()
        try:
            conn.ping(reconnect=True, attempts=1, delay=0)
        finally:
            conn.close()
    if ensure_tables:
        ensure_tables_exist()


def ensure_tables_exist():
    global _tables_ensured
    if _tables_ensured:
        return
    _tables_ensure_lock.acquire()
    if _tables_ensured:
        _tables_ensure_lock.release()
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        def _ensure_column(table_name, column_name, column_sql):
            cursor.execute(
                """
                SELECT 1
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = %s
                  AND COLUMN_NAME = %s
                LIMIT 1
                """,
                (DB_CONFIG["database"], table_name, column_name),
            )
            if cursor.fetchone() is None:
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")

        for sql in CREATE_TABLE_SQL_LIST:
            cursor.execute(sql)
        _ensure_column(
            "t_mt_py_background_task",
            "interface_code",
            "interface_code VARCHAR(128) NULL COMMENT '接口编码'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "task_kind",
            "task_kind VARCHAR(32) NOT NULL DEFAULT 'internal' COMMENT '任务分类'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "project_id",
            "project_id BIGINT NULL COMMENT '项目ID'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "execute_count",
            "execute_count INT NOT NULL DEFAULT 0 COMMENT '已执行次数'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "max_execute_count",
            "max_execute_count INT NOT NULL DEFAULT 3 COMMENT '最大执行次数'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "handler_module",
            "handler_module VARCHAR(255) NULL COMMENT '处理函数模块'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "handler_name",
            "handler_name VARCHAR(128) NULL COMMENT '处理函数名称'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "pass_task_id",
            "pass_task_id TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否传递task_id'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "request_json",
            "request_json LONGTEXT NULL COMMENT '请求JSON'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "kwargs_json",
            "kwargs_json LONGTEXT NULL COMMENT '执行参数JSON'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "progress_json",
            "progress_json LONGTEXT NULL COMMENT '进度JSON'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "result_json",
            "result_json LONGTEXT NULL COMMENT '结果JSON'",
        )
        _ensure_column(
            "t_mt_py_background_task",
            "error_json",
            "error_json LONGTEXT NULL COMMENT '错误JSON'",
        )
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            LIMIT 1
            """,
            (DB_CONFIG["database"], "t_mt_py_fem_shell_property", "element_set"),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                """
                ALTER TABLE t_mt_py_fem_shell_property
                ADD COLUMN element_set VARCHAR(255) NULL COMMENT '单元集名称'
                """
            )
        _ensure_column(
            "t_mt_py_fem_modal_correlation",
            "flip",
            "flip BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否需要相位翻转'",
        )
        _ensure_column(
            "t_mt_py_fem_analysis_run",
            "source_kind",
            "source_kind VARCHAR(32) NULL COMMENT '灵敏度结果来源类型'",
        )
        _ensure_column(
            "t_mt_py_fem_analysis_run",
            "op2_path",
            "op2_path VARCHAR(1024) NULL COMMENT 'OP2结果文件路径'",
        )
        _ensure_column(
            "t_mt_py_fem_analysis_run",
            "matrix_path",
            "matrix_path VARCHAR(1024) NULL COMMENT '矩阵结果文件路径'",
        )
        _ensure_column(
            "t_mt_py_fem_analysis_run",
            "bdf_path",
            "bdf_path VARCHAR(1024) NULL COMMENT 'BDF文件路径'",
        )
        _ensure_column(
            "t_mt_py_fem_analysis_run",
            "metadata_path",
            "metadata_path VARCHAR(1024) NULL COMMENT '元数据文件路径'",
        )
        _ensure_column(
            "t_mt_py_fem_response_def",
            "response_type",
            "response_type VARCHAR(32) NULL COMMENT '响应类型'",
        )
        _ensure_column(
            "t_mt_py_fem_response_def",
            "mode_number",
            "mode_number INT NULL COMMENT '模态阶次'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "param_type",
            "param_type VARCHAR(32) NULL COMMENT '参数类型'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "material_id",
            "material_id BIGINT NULL COMMENT '材料ID'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "property_id",
            "property_id BIGINT NULL COMMENT '属性ID'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "element_id",
            "element_id BIGINT NULL COMMENT '单元ID'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "source_material_id",
            "source_material_id BIGINT NULL COMMENT '源材料ID'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "source_property_id",
            "source_property_id BIGINT NULL COMMENT '源属性ID'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "initial_value",
            "initial_value DOUBLE NULL COMMENT '初始值'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "lower_bound",
            "lower_bound DOUBLE NULL COMMENT '下界'",
        )
        _ensure_column(
            "t_mt_py_fem_parameter_def",
            "upper_bound",
            "upper_bound DOUBLE NULL COMMENT '上界'",
        )
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            LIMIT 1
            """,
            (DB_CONFIG["database"], "t_mt_py_fem_layered_property", "Offset"),
        )
        layered_offset_exists = cursor.fetchone() is not None
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            LIMIT 1
            """,
            (DB_CONFIG["database"], "t_mt_py_fem_layered_property", "Offset_L"),
        )
        layered_offset_l_exists = cursor.fetchone() is not None
        if layered_offset_exists and not layered_offset_l_exists:
            cursor.execute(
                """
                ALTER TABLE t_mt_py_fem_layered_property
                CHANGE COLUMN `Offset` Offset_L DOUBLE NULL COMMENT 'Offset'
                """
            )
        conn.commit()
        _tables_ensured = True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
        if _tables_ensure_lock.locked():
            _tables_ensure_lock.release()


def clear_unv_tables(cursor, pid):
    cursor.execute(f"DELETE FROM t_mt_py_test_node WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_element WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_frequency WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_shape WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_shape_imag WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_shape_real WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_static_result WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_response_catalog WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = {pid}")


def clear_fem_tables(cursor, pid):
    cursor.execute(f"DELETE FROM t_mt_py_fem_coord WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_material_overview WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_isotropic WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_ortho2d WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_aniso3d WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_shell_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_beam_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_solid_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_layered_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_boundary WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_transform_operation WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_static_shape_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_quantity_set_capability WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_selected_parameter WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_sol200_parameter_config WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_parameter_definition WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_parameter_target WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_design_response_catalog WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_sol200_response_config WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_octree_cache WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_response_catalog WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_modal_result WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_static_result WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_response_difference WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_parameter_variation WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_tracking_iteration WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_tracking_value WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_bayesian_iteration_metric WHERE project_id = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_relevance_tracking WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_analysis_error WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_model_update_static_result WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_manual_response WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dac_dsf WHERE pid = {pid}")
